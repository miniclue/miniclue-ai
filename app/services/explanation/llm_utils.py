import base64
import logging
import asyncio
from typing import Optional
import uuid

from pydantic import ValidationError

from app.schemas.explanation import ExplanationResult
from app.utils.config import Settings
from app.utils.posthog_client import create_posthog_client
from app.utils.secret_manager import InvalidAPIKeyError


# Initialize settings
settings = Settings()


async def generate_explanation(
    slide_image_bytes: bytes,
    slide_number: int,
    total_slides: int,
    prev_slide_text: Optional[str],
    next_slide_text: Optional[str],
    lecture_id: str,
    slide_id: str,
    customer_identifier: str,
    user_api_key: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
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
        {"type": "input_image", "image_url": f"data:image/png;base64,{base64_image}"},
        {
            "type": "input_text",
            "text": f"""
Slide {slide_number} of {total_slides}

Context from adjacent slides:
- Previous slide raw text: "{prev_slide_text or 'N/A'}"
- Next slide raw text: "{next_slide_text or 'N/A'}"

Your task:
Act as a warm, approachable professor explaining this slide to students so they fully understand it. 
Use plain, friendly language, ask rhetorical questions, and connect ideas to relatable examples. 
If not the first slide, weave in a natural transition from the previous slide’s raw text. 
Focus only on the visible content of this slide.

Output exactly one valid JSON object with:
- slide_purpose ("cover", "header", or "content")
- one_liner (≤ 25 words, the main idea)
- explanation (engaging Markdown with LaTeX if needed)
        """,
        },
    ]

    # Create client with user's API key
    client = create_posthog_client(user_api_key, provider="openai")

    try:
        # Add timeout to prevent hanging
        response = await asyncio.wait_for(
            client.responses.parse(
                model=settings.explanation_model,
                instructions=system_prompt,
                input=[{"role": "user", "content": user_message_content}],
                # reasoning={"effort": "minimal"},
                # text={"verbosity": "high"},
                text_format=ExplanationResult,
                posthog_distinct_id=customer_identifier,
                posthog_trace_id=lecture_id,
                posthog_properties={
                    "service": "explanation",
                    "lecture_id": lecture_id,
                    "slide_id": slide_id,
                    "slide_number": slide_number,
                    "total_slides": total_slides,
                    "customer_name": name,
                    "customer_email": email,
                },
            ),
            timeout=60.0,  # 60 second timeout
        )

        # Prefer structured output parsed by the client
        result = response.output_parsed
        if result is None:
            logging.warning(
                "output_parsed was None; retrying with explicit structured request"
            )
            retry_response = await asyncio.wait_for(
                client.responses.parse(
                    model=settings.explanation_model,
                    input=[
                        {
                            "role": "user",
                            "content": "Return a valid JSON object matching the 'ExplanationResult' schema.",
                        }
                    ],
                    reasoning={"effort": "low"},
                    text={"verbosity": "low"},
                    text_format=ExplanationResult,
                    previous_response_id=response.id,
                    posthog_distinct_id=customer_identifier,
                    posthog_trace_id=lecture_id,
                    posthog_properties={
                        "service": "explanation",
                        "lecture_id": lecture_id,
                        "slide_id": slide_id,
                        "slide_number": slide_number,
                        "total_slides": total_slides,
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
                    "Retry also failed to produce structured output. Returning fallback."
                )
                fallback_result = ExplanationResult(
                    explanation="Unable to generate explanation due to technical difficulties. Please try again.",
                    one_liner="Technical error occurred during explanation generation.",
                    slide_purpose="error",
                )
                fallback_metadata = {
                    "model": retry_response.model,
                    "usage": (
                        retry_response.usage.model_dump()
                        if retry_response.usage
                        else None
                    ),
                    "response_id": retry_response.id,
                    "fallback": True,
                }
                return fallback_result, fallback_metadata

        metadata = {
            "model": response.model,
            "usage": response.usage.model_dump() if response.usage else None,
            "response_id": response.id,
        }

        return result, metadata

    except asyncio.TimeoutError:
        logging.error("Timeout occurred while calling the AI model")
        raise ValueError("AI model request timed out")
    except ValidationError as e:
        logging.error(f"Failed to validate AI response into Pydantic model: {e}")
        raise
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
        logging.error(f"An unexpected error occurred while calling OpenAI: {e}")
        raise


def mock_generate_explanation(
    slide_image_bytes: bytes,
    slide_number: int,
    total_slides: int,
    prev_slide_text: Optional[str],
    next_slide_text: Optional[str],
    lecture_id: str,
    slide_id: str,
) -> tuple[ExplanationResult, dict]:
    """
    Returns a mock explanation result containing the full prompt that would have
    been sent to the AI model.
    """

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
        "response_id": f"mock_response_{uuid.uuid4()}",
        "mock": True,
    }

    metadata.update(
        {
            "environment": settings.app_env,
            "service": "explanation",
            "lecture_id": lecture_id,
            "slide_id": slide_id,
        }
    )
    return result, metadata
