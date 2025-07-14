import base64
import json
import logging
import re
from typing import Optional
import uuid

from openai import AsyncOpenAI
from pydantic import ValidationError

from app.schemas.explanation import ExplanationResult
from app.utils.config import Settings


# Initialize OpenAI client
settings = Settings()
client = AsyncOpenAI(
    api_key=settings.gemini_api_key, base_url=settings.gemini_api_base_url
)


async def generate_explanation(
    slide_image_bytes: bytes,
    slide_number: int,
    total_slides: int,
    prev_slide_text: Optional[str],
    next_slide_text: Optional[str],
) -> tuple[ExplanationResult, dict]:
    """
    Generates an explanation for a slide using a multi-modal LLM.

    Args:
        slide_image_bytes: The byte content of the slide image.
        slide_number: The number of the current slide.
        total_slides: The total number of slides in the lecture.
        prev_slide_text: The raw text from the previous slide, if available.
        next_slide_text: The raw text from the next slide, if available.

    Returns:
        A tuple containing an ExplanationResult object and a metadata dictionary.
    """
    logging.info("Generating explanation from AI for a slide.")

    # Encode image to base64
    base64_image = base64.b64encode(slide_image_bytes).decode("utf-8")

    # Load system prompt
    try:
        with open("app/services/explanation/prompt.md", "r", encoding="utf-8") as f:
            system_prompt = f.read()
    except FileNotFoundError:
        logging.error("Explanation prompt file not found.")
        raise

    # Construct user message
    user_message_content = [
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{base64_image}"},
        },
        {
            "type": "text",
            "text": f"""
Slide {slide_number} of {total_slides}.

Context from adjacent slides:
- Previous slide text: "{prev_slide_text or 'N/A'}"
- Next slide text: "{next_slide_text or 'N/A'}"

Please provide your explanation based on the system prompt's instructions.
            """,
        },
    ]

    try:
        response = await client.chat.completions.create(
            model=settings.explanation_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message_content},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
            max_tokens=2048,
        )

        response_content = response.choices[0].message.content
        if not response_content:
            raise ValueError("Received an empty response from the AI model.")

        try:
            # First attempt to parse the JSON directly
            data = json.loads(response_content)
            result = ExplanationResult.model_validate(data)
        except json.JSONDecodeError:
            # If parsing fails, it's often due to unescaped backslashes.
            # Attempt to fix this common error and retry parsing.
            logging.warning(
                "JSON decoding failed. Attempting to fix invalid backslash escapes and retry."
            )

            # This regex finds backslashes that are NOT followed by a valid JSON escape character
            # (", \, /, b, f, n, r, t, u) and properly escapes them.
            sanitized_content = re.sub(
                r'\\([^"\\/bfnrtu])', r"\\\\\1", response_content
            )

            try:
                data = json.loads(sanitized_content)
                result = ExplanationResult.model_validate(data)
                logging.info("Successfully parsed JSON after sanitizing backslashes.")
            except (json.JSONDecodeError, ValidationError) as e:
                logging.error(
                    f"Still failed to parse JSON after sanitizing: {sanitized_content}",
                    exc_info=True,
                )
                raise ValueError(
                    f"Failed to decode JSON from AI response even after sanitizing: {e}"
                ) from e

        logging.info("Successfully generated and parsed explanation from AI.")

        metadata = {
            "model": response.model,
            "usage": response.usage.model_dump() if response.usage else None,
            "finish_reason": response.choices[0].finish_reason,
            "response_id": response.id,
        }

        return result, metadata

    except ValidationError as e:
        logging.error(f"Failed to validate AI response into Pydantic model: {e}")
        raise
    except Exception as e:
        logging.error(f"An unexpected error occurred while calling OpenAI: {e}")
        raise


def mock_generate_explanation(
    slide_image_bytes: bytes,
    slide_number: int,
    total_slides: int,
    prev_slide_text: Optional[str],
    next_slide_text: Optional[str],
) -> tuple[ExplanationResult, dict]:
    """
    Returns a mock explanation result containing the full prompt that would have
    been sent to the AI model.
    """
    logging.info("Generating MOCK explanation with full prompt for a slide.")

    # Load system prompt
    try:
        with open("app/services/explanation/prompt.md", "r", encoding="utf-8") as f:
            system_prompt = f.read()
    except FileNotFoundError:
        logging.error("Explanation prompt file not found for mock generation.")
        system_prompt = "[System Prompt Not Found]"

    # Construct the text part of the user prompt
    user_text_prompt = f"""
Slide {slide_number} of {total_slides}.

Context from adjacent slides:
- Previous slide text: "{prev_slide_text or 'N/A'}"
- Next slide text: "{next_slide_text or 'N/A'}"

Please provide your explanation based on the system prompt's instructions.
"""

    # Combine the prompts into a single string for debugging purposes
    full_prompt_for_debug = f"""
---
# SYSTEM PROMPT
---
{system_prompt}

---
# USER PROMPT
---
### Image Data:
(Image with {len(slide_image_bytes)} bytes would be here)

### Text Data:
{user_text_prompt}
"""

    result = ExplanationResult(
        explanation=full_prompt_for_debug,
        one_liner="MOCK: The full prompt that would be sent to the AI is in the main content area.",
        slide_purpose="mock_prompt_debug",
    )

    metadata = {
        "model": "mock-explanation-model",
        "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        "finish_reason": "stop",
        "response_id": f"mock_response_{uuid.uuid4()}",
        "mock": True,
    }

    return result, metadata
