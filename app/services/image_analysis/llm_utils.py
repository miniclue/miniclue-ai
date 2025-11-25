import base64
import asyncio
import logging
from io import BytesIO
from PIL import Image
from typing import Optional
import uuid

from pydantic import ValidationError

from app.schemas.image_analysis import ImageAnalysisResult
from app.utils.config import Settings
from app.utils.posthog_client import create_posthog_client
from app.utils.secret_manager import InvalidAPIKeyError

# Initialize settings and client at the module level
settings = Settings()


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
    Analyzes an image using OpenAI Responses API with structured outputs
    """
    try:
        with open("app/services/image_analysis/prompt.md", "r", encoding="utf-8") as f:
            system_prompt = f.read()
    except FileNotFoundError:
        logging.error("Image analysis prompt file not found for mock generation.")
        raise

    image = Image.open(BytesIO(image_bytes))
    image_mime_type = f"image/{image.format.lower() or 'png'}"

    base64_image = base64.b64encode(image_bytes).decode("utf-8")
    data_url = f"data:{image_mime_type};base64,{base64_image}"

    # Create client with user's API key
    client = create_posthog_client(user_api_key, provider="openai")

    try:
        response = await asyncio.wait_for(
            client.responses.parse(
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
                reasoning={"effort": "low"},
                text={"verbosity": "low"},
                text_format=ImageAnalysisResult,
                posthog_distinct_id=customer_identifier,
                posthog_trace_id=lecture_id,
                posthog_properties={
                    "service": "image_analysis",
                    "lecture_id": lecture_id,
                    "slide_image_id": slide_image_id,
                    "image_size_bytes": len(image_bytes),
                    "customer_name": name,
                    "customer_email": email,
                },
            ),
            timeout=60.0,
        )

        result = response.output_parsed
        if result is None:
            logging.warning(
                "Image analysis output_parsed was None; retrying with explicit instruction"
            )
            retry_response = await asyncio.wait_for(
                client.responses.parse(
                    model=settings.image_analysis_model,
                    input=[
                        {
                            "role": "user",
                            "content": "Return a valid JSON object matching the 'ImageAnalysisResult' schema.",
                        }
                    ],
                    reasoning={"effort": "low"},
                    text={"verbosity": "low"},
                    text_format=ImageAnalysisResult,
                    previous_response_id=response.id,
                    posthog_distinct_id=customer_identifier,
                    posthog_trace_id=lecture_id,
                    posthog_properties={
                        "service": "image_analysis",
                        "lecture_id": lecture_id,
                        "slide_image_id": slide_image_id,
                        "image_size_bytes": len(image_bytes),
                        "customer_name": name,
                        "customer_email": email,
                        "retry": True,
                    },
                ),
                timeout=30.0,
            )
            result = retry_response.output_parsed
            if result is None:
                logging.error(
                    "Retry failed to produce structured output; creating fallback ImageAnalysisResult"
                )
                result = ImageAnalysisResult(type="content", ocr_text="", alt_text="")
                metadata = {
                    "model": retry_response.model,
                    "usage": (
                        retry_response.usage.model_dump()
                        if retry_response.usage
                        else None
                    ),
                    "response_id": retry_response.id,
                    "fallback": True,
                }
                return result, metadata

        metadata = {
            "model": response.model,
            "usage": response.usage.model_dump() if response.usage else None,
            "response_id": response.id,
        }
        return result, metadata

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
        # Check if it's an authentication error (invalid API key)
        error_str = str(e).lower()
        if (
            "authentication" in error_str
            or "unauthorized" in error_str
            or "invalid api key" in error_str
            or "401" in error_str
        ):
            logging.error(f"OpenAI authentication error (invalid API key): {e}")
            raise InvalidAPIKeyError(f"Invalid API key: {str(e)}") from e
        logging.error(
            "An unexpected error occurred during image analysis.", exc_info=True
        )
        raise


def mock_analyze_image(
    image_bytes: bytes, lecture_id: str, slide_image_id: str
) -> tuple[ImageAnalysisResult, dict]:
    """
    Mock function for image analysis for development and testing.
    """
    # Create a mock result and metadata for testing
    result = ImageAnalysisResult(
        type="content",
        ocr_text="This is mock OCR text from the image.",
        alt_text="This is a mock alt text describing the image.",
    )
    metadata = {
        "model": "mock-image-analysis-model",
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "response_id": f"mock_response_{uuid.uuid4()}",
        "mock": True,
    }
    return result, metadata
