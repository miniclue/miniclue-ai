import base64
import asyncio
import logging
from io import BytesIO
from PIL import Image
from typing import Optional

import litellm
from pydantic import ValidationError

from app.schemas.image_analysis import ImageAnalysisResult
from app.utils.config import Settings
from app.utils.secret_manager import InvalidAPIKeyError

# Constants
INITIAL_REQUEST_TIMEOUT = 60.0
RETRY_REQUEST_TIMEOUT = 30.0
PROMPT_FILE_PATH = "app/services/image_analysis/prompt.md"
RETRY_PROMPT = "Return a valid JSON object matching the 'ImageAnalysisResult' schema."

# Initialize settings
settings = Settings()


def _load_system_prompt() -> str:
    """Loads the system prompt from the prompt file."""
    try:
        with open(PROMPT_FILE_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logging.error("Image analysis prompt file not found.")
        raise


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
        "service": "image_analysis",
        "lecture_id": lecture_id,
        "slide_image_id": slide_image_id,
        "image_size_bytes": image_size_bytes,
        "customer_name": name,
        "customer_email": email,
    }
    if is_retry:
        properties["retry"] = True
    return properties


def _extract_metadata(response, is_fallback: bool = False) -> dict:
    """Extracts metadata from LLM response."""
    metadata = {
        "model": response.model,
        "usage": response.usage.model_dump() if response.usage else None,
        "response_id": response.id,
    }
    if is_fallback:
        metadata["fallback"] = True
    return metadata


def _create_fallback_response() -> ImageAnalysisResult:
    """Creates a fallback response when LLM fails to produce structured output."""
    return ImageAnalysisResult(type="content", ocr_text="", alt_text="")


def _is_authentication_error(error: Exception) -> bool:
    """Checks if the error is related to authentication/invalid API key."""
    error_str = str(error).lower()
    auth_indicators = ["authentication", "unauthorized", "invalid api key", "401"]
    return any(indicator in error_str for indicator in auth_indicators)


async def analyze_image(
    image_bytes: bytes,
    lecture_id: str,
    slide_image_id: str,
    customer_identifier: str,
    user_api_key: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> tuple[ImageAnalysisResult, dict]:
    """
    Analyzes an image using OpenAI Responses API with structured outputs.

    Args:
        image_bytes: The byte content of the image to analyze.
        lecture_id: Unique identifier for the lecture.
        slide_image_id: Unique identifier for the slide image.
        customer_identifier: Unique identifier for the customer.
        user_api_key: User's API key for the LLM provider.
        name: Optional customer name for tracking.
        email: Optional customer email for tracking.

    Returns:
        A tuple containing an ImageAnalysisResult object and a metadata dictionary.

    Raises:
        ValueError: If the request times out or response validation fails.
        InvalidAPIKeyError: If the API key is invalid.
        FileNotFoundError: If the prompt file is not found.
    """
    system_prompt = _load_system_prompt()

    image = Image.open(BytesIO(image_bytes))
    image_mime_type = f"image/{image.format.lower() or 'png'}"

    base64_image = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:{image_mime_type};base64,{base64_image}"

    posthog_properties = _create_posthog_properties(
        lecture_id, slide_image_id, len(image_bytes), name, email
    )

    litellm.success_callback = ["posthog"]

    try:
        response = await asyncio.wait_for(
            litellm.aresponses(
                model=settings.image_analysis_model,
                instructions=system_prompt,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_image", "image_url": data_url},
                            {
                                "type": "input_text",
                                "text": "Analyze the image per the system prompt.",
                            },
                        ],
                    },
                ],
                text_format=ImageAnalysisResult,
                api_key=user_api_key,
                metadata={
                    "user_id": customer_identifier,
                    "$ai_trace_id": lecture_id,
                    **posthog_properties,
                },
            ),
            timeout=INITIAL_REQUEST_TIMEOUT,
        )

        result = response.output_parsed
        if result is not None:
            return result, _extract_metadata(response)

        # Retry with explicit schema request
        logging.warning("Initial response missing structured output. Retrying...")
        retry_properties = _create_posthog_properties(
            lecture_id, slide_image_id, len(image_bytes), name, email, is_retry=True
        )

        retry_response = await asyncio.wait_for(
            litellm.aresponses(
                model=settings.image_analysis_model,
                input=[{"role": "user", "content": RETRY_PROMPT}],
                text_format=ImageAnalysisResult,
                previous_response_id=response.id,
                api_key=user_api_key,
                metadata={
                    "user_id": customer_identifier,
                    "$ai_trace_id": lecture_id,
                    **retry_properties,
                },
            ),
            timeout=RETRY_REQUEST_TIMEOUT,
        )

        result = retry_response.output_parsed
        if result is not None:
            return result, _extract_metadata(retry_response)

        # Both attempts failed - return fallback
        logging.error("Retry failed to produce structured output. Returning fallback.")
        return _create_fallback_response(), _extract_metadata(
            retry_response, is_fallback=True
        )

    except asyncio.TimeoutError:
        logging.error("Timeout occurred while calling the AI model for image analysis")
        raise ValueError("AI model request timed out")
    except ValidationError as e:
        logging.error(
            "Image analysis response did not match Pydantic model", exc_info=True
        )
        raise ValueError(
            "Image analysis response did not match the expected format."
        ) from e
    except Exception as e:
        if _is_authentication_error(e):
            logging.error(f"OpenAI authentication error (invalid API key): {e}")
            raise InvalidAPIKeyError(f"Invalid API key: {str(e)}") from e
        logging.error(
            "An unexpected error occurred during image analysis.", exc_info=True
        )
        raise
