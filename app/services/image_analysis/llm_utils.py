import base64
import asyncio
import logging
import time
from io import BytesIO
from typing import Optional, TYPE_CHECKING
from pydantic import ValidationError

from app.schemas.image_analysis import ImageAnalysisResult
from app.utils.config import Settings
from app.utils.secret_manager import InvalidAPIKeyError
from app.utils.model_provider_mapping import get_provider_for_model
from app.utils.llm_utils import (
    extract_metadata,
    is_authentication_error,
)
from app.utils.posthog_client import (
    get_posthog_client,
)

if TYPE_CHECKING:
    from posthog.ai.openai import AsyncOpenAI

# Constants
INITIAL_REQUEST_TIMEOUT = 60.0
RETRY_REQUEST_TIMEOUT = 30.0
MAX_RETRIES = 2

# Initialize settings
settings = Settings()


def _create_posthog_properties(
    lecture_id: str,
    slide_image_id: str,
    image_size_bytes: int,
    name: Optional[str],
    email: Optional[str],
    is_retry: bool = False,
) -> dict:
    """Creates PostHog properties dictionary for tracking."""
    properties = {
        "lecture_id": lecture_id,
        "slide_image_id": slide_image_id,
        "image_size_bytes": image_size_bytes,
        "customer_name": name,
        "customer_email": email,
    }
    if is_retry:
        properties["retry"] = True
    return properties


def _create_fallback_response() -> ImageAnalysisResult:
    """Creates a fallback response when LLM fails to produce structured output."""
    return ImageAnalysisResult(type="content", ocr_text="", alt_text="")


def _capture_posthog_event(
    customer_identifier: str,
    lecture_id: str,
    response,
    messages: list,
    latency: float,
    posthog_properties: dict,
) -> None:
    """Captures PostHog AI generation event."""
    posthog_client = get_posthog_client()
    if not posthog_client:
        return

    try:
        usage = getattr(response, "usage", None)
        input_tokens = getattr(usage, "prompt_tokens", None) if usage else None
        output_tokens = getattr(usage, "completion_tokens", None) if usage else None

        output_choices = []
        if hasattr(response, "choices") and response.choices:
            for choice in response.choices:
                choice_dict = {"role": "assistant"}
                if hasattr(choice, "message") and choice.message:
                    content = getattr(choice.message, "content", None)
                    if content:
                        choice_dict["content"] = [{"type": "text", "text": content}]
                output_choices.append(choice_dict)

        posthog_client.capture(
            distinct_id=customer_identifier,
            event="$ai_generation",
            properties={
                "$ai_trace_id": lecture_id,
                "$ai_span_name": "lecture_image_analysis",
                "$ai_model": getattr(response, "model", settings.image_analysis_model),
                "$ai_provider": get_provider_for_model(settings.image_analysis_model),
                "$ai_input": messages,
                "$ai_input_tokens": input_tokens,
                "$ai_output_choices": output_choices,
                "$ai_output_tokens": output_tokens,
                "$ai_latency": latency,
                **posthog_properties,
            },
        )
    except Exception as e:
        logging.warning(f"Failed to capture PostHog event: {e}")


async def analyze_image(
    image_bytes: bytes,
    lecture_id: str,
    slide_image_id: str,
    customer_identifier: str,
    client: "AsyncOpenAI",
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> tuple[ImageAnalysisResult, dict]:
    """
    Analyzes an image using an LLM provider with JSON structured outputs.

    Args:
        image_bytes: The byte content of the image to analyze.
        lecture_id: Unique identifier for the lecture.
        slide_image_id: Unique identifier for the slide image.
        customer_identifier: Unique identifier for the customer.
        client: PostHog-wrapped OpenAI client.
        name: Optional customer name for tracking.
        email: Optional customer email for tracking.

    Returns:
        A tuple containing an ImageAnalysisResult object and a metadata dictionary.

    Raises:
        ValueError: If the request times out or response validation fails.
        InvalidAPIKeyError: If the API key is invalid.
        FileNotFoundError: If the prompt file is not found.
    """

    from PIL import Image

    image = Image.open(BytesIO(image_bytes))
    image_mime_type = f"image/{image.format.lower() or 'png'}"

    base64_image = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:{image_mime_type};base64,{base64_image}"

    posthog_properties = _create_posthog_properties(
        lecture_id, slide_image_id, len(image_bytes), name, email
    )

    messages = [
        {
            "role": "developer",
            "content": """You are an image analysis API. Your sole function is to analyze the provided image and return a single, raw JSON object.

You MUST strictly adhere to the following JSON structure:
{
"type": "content" | "decorative",
"ocr_text": "string",
"alt_text": "string"
}

- "type": Classify the image. Use "content" for meaningful information (diagrams, charts, text). Use "decorative" for aesthetics (backgrounds, stock photos).
- "ocr_text": Extract all visible text. Return an empty string if there is no text.
- "alt_text": Write a concise, descriptive alt text for accessibility, explaining the image's content and purpose.

Your response MUST NOT include any explanations, introductory text, or markdown formatting like ```json. It must be ONLY the raw JSON object.""",
        },
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": data_url}},
                {"type": "text", "text": "Analyze the image per the system prompt."},
            ],
        },
    ]

    for _ in range(MAX_RETRIES):
        try:
            start_time = time.time()
            response = await asyncio.wait_for(
                client.chat.completions.parse(
                    model=settings.image_analysis_model,
                    messages=messages,
                    response_format=ImageAnalysisResult,
                ),
                timeout=INITIAL_REQUEST_TIMEOUT,
            )
            latency = time.time() - start_time

            result = response.choices[0].message.parsed
            metadata = extract_metadata(response)
            _capture_posthog_event(
                customer_identifier,
                lecture_id,
                response,
                messages,
                latency,
                posthog_properties,
            )
            return result, metadata

        except asyncio.TimeoutError:
            logging.error(
                "Timeout occurred while calling the AI model for image analysis"
            )
            raise ValueError("AI model request timed out")
        except ValidationError as e:
            logging.error(
                "Image analysis response did not match Pydantic model", exc_info=True
            )
            raise ValueError(
                "Image analysis response did not match the expected format."
            ) from e
        except Exception as e:
            if is_authentication_error(e):
                logging.error(f"OpenAI authentication error (invalid API key): {e}")
                raise InvalidAPIKeyError(f"Invalid API key: {str(e)}") from e
            logging.error(
                "An unexpected error occurred during image analysis.", exc_info=True
            )
            raise

    # Both attempts failed - return fallback
    logging.error("Retry failed to produce structured output. Returning fallback.")
    fallback_metadata = {"is_fallback": True}
    return _create_fallback_response(), fallback_metadata
