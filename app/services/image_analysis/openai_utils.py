import base64
import json
import logging
import re
from io import BytesIO
from PIL import Image
from typing import Optional
import uuid

from pydantic import ValidationError

from app.schemas.image_analysis import ImageAnalysisResult
from app.utils.config import Settings
from app.utils.posthog_client import posthog_gemini_client

# Initialize settings and client at the module level
settings = Settings()


async def analyze_image(
    image_bytes: bytes,
    lecture_id: str,
    slide_image_id: str,
    customer_identifier: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> tuple[ImageAnalysisResult, dict]:
    """
    Analyzes an image using the Gemini API
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

    try:
        logging.info("Sending image to Gemini for analysis...")
        response = await posthog_gemini_client.chat.completions.create(
            model=settings.image_analysis_model,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url},
                        },
                    ],
                },
            ],
            max_tokens=1024,
            temperature=0.1,
            response_format={"type": "json_object"},
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
        )

        response_text = response.choices[0].message.content
        if not response_text:
            raise ValueError("Received empty response from Gemini.")

        logging.info("Received analysis from Gemini.")

        # Strip markdown code fences if present
        if response_text.startswith("```json"):
            response_text = re.sub(
                r"^\s*```json\s*(.*?)\s*```\s*$", r"\1", response_text, flags=re.DOTALL
            )

        try:
            analysis_data = json.loads(response_text)
        except json.JSONDecodeError:
            logging.warning(
                "JSON decoding failed. Attempting to fix invalid backslash escapes and retry."
            )
            sanitized_text = re.sub(r'\\([^"\\/bfnrtu])', r"\\\\\1", response_text)
            try:
                analysis_data = json.loads(sanitized_text)
                logging.info("Successfully parsed JSON after sanitizing backslashes.")
            except json.JSONDecodeError as e:
                logging.error(
                    f"Still failed to parse JSON after sanitizing: {sanitized_text}",
                    exc_info=True,
                )
                raise ValueError(
                    "Gemini response was not valid JSON even after sanitizing."
                ) from e
        # Build result and metadata for logging and database
        result = ImageAnalysisResult(**analysis_data)
        metadata = {
            "model": response.model,
            "usage": response.usage.model_dump() if response.usage else None,
            "finish_reason": response.choices[0].finish_reason,
            "response_id": response.id,
        }
        return result, metadata

    except ValidationError as e:
        logging.error(
            f"Gemini response did not match Pydantic model: {response_text}",
            exc_info=True,
        )
        raise ValueError("Gemini response did not match the expected format.") from e
    except Exception:
        logging.error(
            "An unexpected error occurred during Gemini image analysis.", exc_info=True
        )
        raise


def mock_analyze_image(
    image_bytes: bytes, lecture_id: str, slide_image_id: str
) -> tuple[ImageAnalysisResult, dict]:
    """
    Mock function for image analysis for development and testing.
    """
    logging.info(f"Mocking image analysis for image of size {len(image_bytes)} bytes.")
    # Create a mock result and metadata for testing
    result = ImageAnalysisResult(
        type="content",
        ocr_text="This is mock OCR text from the image.",
        alt_text="This is a mock alt text describing the image.",
    )
    metadata = {
        "model": "mock-image-analysis-model",
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "finish_reason": "stop",
        "response_id": f"mock_response_{uuid.uuid4()}",
        "mock": True,
    }
    return result, metadata
