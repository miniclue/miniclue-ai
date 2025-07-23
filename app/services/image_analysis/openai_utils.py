import base64
import json
import logging
import re
from io import BytesIO
from PIL import Image
from typing import Optional

from openai import AsyncOpenAI
from pydantic import ValidationError

from app.schemas.image_analysis import ImageAnalysisResult
from app.utils.config import Settings

# Initialize settings and client at the module level
settings = Settings()
client = AsyncOpenAI(
    api_key=settings.keywordsai_api_key, base_url=settings.keywordsai_proxy_base_url
)


async def analyze_image(
    image_bytes: bytes,
    lecture_id: str,
    slide_image_id: str,
    customer_identifier: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> ImageAnalysisResult:
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
        response = await client.chat.completions.create(
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
            extra_body={
                "metadata": {
                    "environment": settings.app_env,
                    "service": "image_analysis",
                    "lecture_id": lecture_id,
                    "slide_image_id": slide_image_id,
                },
                "customer_params": {
                    "customer_identifier": customer_identifier,
                    "name": name,
                    "email": email,
                },
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
        return ImageAnalysisResult(**analysis_data)

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
) -> ImageAnalysisResult:
    """
    Mock function for image analysis for development and testing.
    """
    logging.info(f"Mocking image analysis for image of size {len(image_bytes)} bytes.")
    return ImageAnalysisResult(
        type="content",
        ocr_text="This is mock OCR text from the image.",
        alt_text="This is a mock alt text describing the image.",
    )
