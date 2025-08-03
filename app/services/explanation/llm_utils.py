import base64
import json
import logging
import re
import asyncio
from typing import Optional
import uuid

from pydantic import ValidationError

from app.schemas.explanation import ExplanationResult
from app.utils.config import Settings
from app.utils.posthog_client import posthog_gemini_client


# Initialize settings
settings = Settings()


def _extract_json_field(field_name: str, content: str) -> Optional[str]:
    """Extracts a field value from a JSON-like string, handling escaped quotes correctly."""
    # This regex handles escaped quotes inside the string value
    pattern = f'"{field_name}"\\s*:\\s*"((?:\\\\.|[^"])*)"'
    match = re.search(pattern, content)
    if match:
        return match.group(1).replace('\\"', '"')  # Unescape quotes
    return None


def _log_response_debug(
    response_content: str, cleaned_content: str, error: Exception = None
):
    """Helper function to log response details for debugging."""
    logging.info(f"Raw response content: {repr(response_content)}")
    logging.info(f"Cleaned content: {repr(cleaned_content)}")
    if error:
        logging.info(f"Error details: {error}")

    # Log the first and last 100 characters to help identify issues
    if len(response_content) > 200:
        logging.info(f"Response starts with: {repr(response_content[:100])}")
        logging.info(f"Response ends with: {repr(response_content[-100:])}")
    else:
        logging.info(f"Full response: {repr(response_content)}")


async def generate_explanation(
    slide_image_bytes: bytes,
    slide_number: int,
    total_slides: int,
    prev_slide_text: Optional[str],
    next_slide_text: Optional[str],
    lecture_id: str,
    slide_id: str,
    customer_identifier: str,
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
        # Add timeout to prevent hanging
        response = await asyncio.wait_for(
            posthog_gemini_client.chat.completions.create(
                model=settings.explanation_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message_content},
                ],
                response_format={"type": "json_object"},
                temperature=0.7,
                max_tokens=2048,
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

        response_content = response.choices[0].message.content
        if not response_content:
            raise ValueError("Received an empty response from the AI model.")

        # Clean the response content to extract JSON from markdown code blocks if present
        cleaned_content = response_content.strip()

        # Check if the response is wrapped in markdown code blocks
        if cleaned_content.startswith("```json"):
            # Extract content between ```json and ```
            start_marker = "```json"
            end_marker = "```"
            start_idx = cleaned_content.find(start_marker) + len(start_marker)
            end_idx = cleaned_content.rfind(end_marker)
            if start_idx > len(start_marker) - 1 and end_idx > start_idx:
                cleaned_content = cleaned_content[start_idx:end_idx].strip()

        elif cleaned_content.startswith("```"):
            # Extract content between ``` and ```
            start_marker = "```"
            end_marker = "```"
            start_idx = cleaned_content.find(start_marker) + len(start_marker)
            end_idx = cleaned_content.rfind(end_marker)
            if start_idx > len(start_marker) - 1 and end_idx > start_idx:
                cleaned_content = cleaned_content[start_idx:end_idx].strip()

        try:
            # First attempt to parse the JSON directly
            data = json.loads(cleaned_content)
            result = ExplanationResult.model_validate(data)
        except json.JSONDecodeError as e:
            logging.error(
                f"Failed to parse JSON on first attempt. Raw AI response: {response_content}"
            )
            _log_response_debug(response_content, cleaned_content, e)

            # Try multiple sanitization strategies
            sanitized_content = cleaned_content

            # Strategy 1: Fix common backslash issues
            logging.info("Attempting Strategy 1: Fix backslash issues")
            sanitized_content = re.sub(
                r'\\([^"\\/bfnrtu])', r"\\\\\1", sanitized_content
            )

            # Strategy 2: Remove any trailing commas before closing braces/brackets
            logging.info("Attempting Strategy 2: Remove trailing commas")
            sanitized_content = re.sub(r",(\s*[}\]])", r"\1", sanitized_content)

            # Strategy 3: Fix unescaped quotes in strings
            logging.info("Attempting Strategy 3: Fix unescaped quotes")
            sanitized_content = re.sub(r'(?<!\\)"(?=.*":)', r'\\"', sanitized_content)

            # Strategy 4: Remove any markdown formatting that might have slipped through
            logging.info("Attempting Strategy 4: Remove markdown formatting")
            sanitized_content = re.sub(
                r"\*\*(.*?)\*\*", r"\1", sanitized_content
            )  # Remove bold
            sanitized_content = re.sub(
                r"\*(.*?)\*", r"\1", sanitized_content
            )  # Remove italic
            sanitized_content = re.sub(
                r"`(.*?)`", r"\1", sanitized_content
            )  # Remove code

            try:
                data = json.loads(sanitized_content)
                result = ExplanationResult.model_validate(data)
            except (json.JSONDecodeError, ValidationError) as e:
                logging.error(
                    f"Still failed to parse JSON after sanitizing: {sanitized_content}. Error: {e}",
                    exc_info=True,
                )
                _log_response_debug(response_content, cleaned_content, e)
                logging.error(f"Sanitized content: {sanitized_content}")

                # Strategy 5: Try to extract JSON using regex if it's embedded in other text
                logging.info("Attempting Strategy 5: Extract JSON using regex")
                json_pattern = r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}"
                json_matches = re.findall(json_pattern, sanitized_content)
                logging.info(f"Found {len(json_matches)} potential JSON matches")

                json_extracted = False
                for i, match in enumerate(json_matches):
                    try:
                        data = json.loads(match)
                        result = ExplanationResult.model_validate(data)
                        logging.warning(
                            f"Successfully extracted JSON using regex pattern (match {i+1}): {match}"
                        )
                        json_extracted = True
                        break
                    except (json.JSONDecodeError, ValidationError):
                        logging.info(
                            f"Regex match {i+1} failed to parse: {repr(match[:100])}"
                        )
                        continue

                # Strategy 6: Try to fix common JSON issues and retry
                if not json_extracted:
                    logging.info(
                        "Attempting Strategy 6: Fix missing quotes and single quotes"
                    )
                    # Try to fix missing quotes around keys
                    fixed_content = re.sub(r"(\w+):", r'"\1":', sanitized_content)
                    # Try to fix single quotes
                    fixed_content = fixed_content.replace("'", '"')
                    try:
                        data = json.loads(fixed_content)
                        result = ExplanationResult.model_validate(data)
                        logging.warning(
                            f"Successfully parsed JSON after fixing quotes: {fixed_content}"
                        )
                        json_extracted = True
                    except (json.JSONDecodeError, ValidationError):
                        logging.info(f"Strategy 6 failed: {repr(fixed_content[:100])}")
                        pass

                if not json_extracted:
                    # Strategy 7: Try to construct a valid JSON from the response content
                    logging.info(
                        "Attempting Strategy 7: Construct JSON from regex field matches"
                    )
                    try:
                        # Try to extract the three required fields using the helper function
                        explanation = _extract_json_field(
                            "explanation", sanitized_content
                        )
                        one_liner = _extract_json_field("one_liner", sanitized_content)
                        slide_purpose = _extract_json_field(
                            "slide_purpose", sanitized_content
                        )

                        logging.info(
                            f"Field extraction results: explanation={explanation is not None}, one_liner={one_liner is not None}, slide_purpose={slide_purpose is not None}"
                        )

                        if (
                            explanation is not None
                            and one_liner is not None
                            and slide_purpose is not None
                        ):
                            constructed_json = {
                                "explanation": explanation,
                                "one_liner": one_liner,
                                "slide_purpose": slide_purpose,
                            }
                            result = ExplanationResult.model_validate(constructed_json)
                            logging.warning(
                                f"Successfully constructed JSON from regex matches: {constructed_json}"
                            )
                            json_extracted = True
                    except (json.JSONDecodeError, ValidationError) as e:
                        logging.info(
                            f"Failed to construct JSON from regex matches: {e}"
                        )

                if not json_extracted:
                    # If all strategies fail, retry with a more explicit prompt
                    logging.warning(
                        "All JSON parsing strategies failed. Retrying with explicit JSON request"
                    )
                    logging.info(
                        "Making retry call to LLM with explicit JSON formatting instructions"
                    )
                    retry_response = await asyncio.wait_for(
                        posthog_gemini_client.chat.completions.create(
                            model=settings.explanation_model,
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_message_content},
                                {"role": "assistant", "content": response_content},
                                {
                                    "role": "user",
                                    "content": "Your previous response was not valid JSON. Please reply with ONLY a valid JSON object containing exactly these three fields: 'explanation' (string), 'one_liner' (string), and 'slide_purpose' (string). Do not include any other text, markdown formatting, or code blocks.",
                                },
                            ],
                            response_format={"type": "json_object"},
                            temperature=0.0,
                            max_tokens=1024,
                            posthog_distinct_id=customer_identifier,
                            posthog_trace_id=lecture_id,
                        ),
                        timeout=30.0,  # Shorter timeout for retry
                    )
                    retry_content = retry_response.choices[0].message.content.strip()
                    try:
                        data = json.loads(retry_content)
                        result = ExplanationResult.model_validate(data)
                        retry_metadata = {
                            "model": retry_response.model,
                            "usage": (
                                retry_response.usage.model_dump()
                                if retry_response.usage
                                else None
                            ),
                            "finish_reason": retry_response.choices[0].finish_reason,
                            "response_id": retry_response.id,
                            "retry": True,
                        }
                        return result, retry_metadata
                    except Exception as e2:
                        logging.error(
                            f"Retry also failed to parse JSON: {retry_content}",
                            exc_info=True,
                        )
                        # Last resort: create a fallback response
                        logging.error(
                            "Creating fallback response due to persistent JSON parsing failures"
                        )
                        logging.info(
                            f"Fallback created for slide. Raw response was: {repr(response_content[:500])}"
                        )
                        fallback_result = ExplanationResult(
                            explanation="Unable to generate explanation due to technical difficulties. Please try again.",
                            one_liner="Technical error occurred during explanation generation.",
                            slide_purpose="error",
                        )
                        fallback_metadata = {
                            "model": response.model,
                            "usage": (
                                response.usage.model_dump() if response.usage else None
                            ),
                            "finish_reason": response.choices[0].finish_reason,
                            "response_id": response.id,
                            "fallback": True,
                            "error": str(e2),
                        }
                        return fallback_result, fallback_metadata

        metadata = {
            "model": response.model,
            "usage": response.usage.model_dump() if response.usage else None,
            "finish_reason": response.choices[0].finish_reason,
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
        "finish_reason": "stop",
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
