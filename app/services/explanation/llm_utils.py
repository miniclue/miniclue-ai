import base64
import json
import logging
import asyncio
from typing import Optional

import litellm
from pydantic import ValidationError

from app.schemas.explanation import ExplanationResult
from app.utils.config import Settings
from app.utils.secret_manager import InvalidAPIKeyError
from app.utils.llm_utils import extract_text_from_response


# Constants
INITIAL_REQUEST_TIMEOUT = 60.0
RETRY_REQUEST_TIMEOUT = 30.0
LLM_TEMPERATURE = 0.7
RETRY_PROMPT = "Return a valid JSON object matching the 'ExplanationResult' schema."
FALLBACK_ERROR_MESSAGE = (
    "Unable to generate explanation due to technical difficulties. Please try again."
)

# Initialize settings
settings = Settings()


def _create_posthog_properties(
    lecture_id: str,
    slide_id: str,
    slide_number: int,
    total_slides: int,
    name: Optional[str],
    email: Optional[str],
    is_retry: bool = False,
) -> dict:
    """Creates PostHog properties dictionary for tracking."""
    properties = {
        "service": "explanation",
        "lecture_id": lecture_id,
        "slide_id": slide_id,
        "slide_number": slide_number,
        "total_slides": total_slides,
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


def _create_fallback_response() -> ExplanationResult:
    """Creates a fallback response when LLM fails to produce structured output."""
    return ExplanationResult(
        explanation=FALLBACK_ERROR_MESSAGE,
        slide_purpose="error",
    )


def _is_authentication_error(error: Exception) -> bool:
    """Checks if the error is related to authentication/invalid API key."""
    error_str = str(error).lower()
    auth_indicators = ["authentication", "unauthorized", "invalid api key", "401"]
    return any(indicator in error_str for indicator in auth_indicators)


async def generate_explanation(
    slide_image_bytes: bytes,
    slide_number: int,
    total_slides: int,
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
        lecture_id: Unique identifier for the lecture.
        slide_id: Unique identifier for the slide.
        customer_identifier: Unique identifier for the customer.
        user_api_key: User's API key for the LLM provider.
        name: Optional customer name for tracking.
        email: Optional customer email for tracking.

    Returns:
        A tuple containing an ExplanationResult object and a metadata dictionary.

    Raises:
        ValueError: If the request times out.
        InvalidAPIKeyError: If the API key is invalid.
        ValidationError: If the response cannot be validated.
    """
    base64_image = base64.b64encode(slide_image_bytes).decode("utf-8")

    user_message_content = [
        {"type": "input_image", "image_url": f"data:image/png;base64,{base64_image}"},
        {"type": "input_text", "text": "Explain"},
    ]

    posthog_properties = _create_posthog_properties(
        lecture_id, slide_id, slide_number, total_slides, name, email
    )

    litellm.success_callback = ["posthog"]

    try:
        response = await asyncio.wait_for(
            litellm.aresponses(
                model=settings.explanation_model,
                input=[{"role": "user", "content": user_message_content}],
                text_format=ExplanationResult,
                temperature=LLM_TEMPERATURE,
                api_key=user_api_key,
                metadata={
                    "user_id": customer_identifier,
                    "$ai_trace_id": lecture_id,
                    **posthog_properties,
                },
            ),
            timeout=INITIAL_REQUEST_TIMEOUT,
        )

        # Extract and parse structured output
        response_text = extract_text_from_response(response)
        if response_text:
            try:
                result = ExplanationResult.model_validate_json(response_text)
                return result, _extract_metadata(response)
            except (json.JSONDecodeError, ValidationError):
                logging.warning(
                    "Failed to parse initial response as structured output. Retrying..."
                )

        # Retry with explicit schema request
        retry_properties = _create_posthog_properties(
            lecture_id, slide_id, slide_number, total_slides, name, email, is_retry=True
        )

        retry_response = await asyncio.wait_for(
            litellm.aresponses(
                model=settings.explanation_model,
                input=[{"role": "user", "content": RETRY_PROMPT}],
                text_format=ExplanationResult,
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

        # Extract and parse structured output from retry
        retry_text = extract_text_from_response(retry_response)
        if retry_text:
            try:
                result = ExplanationResult.model_validate_json(retry_text)
                return result, _extract_metadata(retry_response)
            except (json.JSONDecodeError, ValidationError):
                logging.warning("Failed to parse retry response as structured output.")

        # Both attempts failed - return fallback
        logging.error("Retry failed to produce structured output. Returning fallback.")
        return _create_fallback_response(), _extract_metadata(
            retry_response, is_fallback=True
        )

    except asyncio.TimeoutError:
        logging.error("Timeout occurred while calling the AI model")
        raise ValueError("AI model request timed out")
    except ValidationError as e:
        logging.error(f"Failed to validate AI response into Pydantic model: {e}")
        raise
    except Exception as e:
        if _is_authentication_error(e):
            logging.error(f"OpenAI authentication error (invalid API key): {e}")
            raise InvalidAPIKeyError(f"Invalid API key: {str(e)}") from e
        logging.error(f"An unexpected error occurred while calling OpenAI: {e}")
        raise
