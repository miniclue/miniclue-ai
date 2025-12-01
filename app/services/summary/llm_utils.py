import logging
from typing import Optional

from app.utils.config import Settings
from app.utils.posthog_client import create_posthog_client
from app.utils.secret_manager import InvalidAPIKeyError

# Constants
PROMPT_FILE_PATH = "app/services/summary/prompt.md"
EMPTY_SUMMARY_ERROR_MESSAGE = "Error: The AI model returned an empty summary."

# Initialize settings
settings = Settings()


def _load_system_prompt() -> str:
    """Loads the system prompt from the prompt file."""
    try:
        with open(PROMPT_FILE_PATH, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        logging.error("Summary prompt file not found.")
        raise


def _format_explanations(explanations: list[str]) -> str:
    """Formats explanations into a numbered list for the prompt."""
    return "\n".join(f"{i}. {exp}" for i, exp in enumerate(explanations, 1))


def _create_user_content(formatted_explanations: str) -> str:
    """Creates the user content string with formatted explanations."""
    return f"""
        --- START OF SLIDE DATA ---
        {formatted_explanations}
        --- END OF SLIDE DATA ---
        
        Please generate the comprehensive study guide now, following all the instructions in the System Prompt.
    """


def _create_posthog_properties(
    lecture_id: str,
    explanations_count: int,
    name: Optional[str],
    email: Optional[str],
) -> dict:
    """Creates PostHog properties dictionary for tracking."""
    return {
        "service": "summary",
        "lecture_id": lecture_id,
        "explanations_count": explanations_count,
        "customer_name": name,
        "customer_email": email,
    }


def _extract_metadata(response) -> dict:
    """Extracts metadata from LLM response."""
    return {
        "model": response.model,
        "usage": response.usage.model_dump() if response.usage else None,
        "response_id": response.id,
    }


def _is_authentication_error(error: Exception) -> bool:
    """Checks if the error is related to authentication/invalid API key."""
    error_str = str(error).lower()
    auth_indicators = ["authentication", "unauthorized", "invalid api key", "401"]
    return any(indicator in error_str for indicator in auth_indicators)


async def generate_summary(
    explanations: list[str],
    lecture_id: str,
    customer_identifier: str,
    user_api_key: str,
    name: Optional[str] = None,
    email: Optional[str] = None,
) -> tuple[str, dict]:
    """
    Generates a comprehensive lecture summary using an AI model.

    Args:
        explanations: List of slide explanations to summarize.
        lecture_id: Unique identifier for the lecture.
        customer_identifier: Unique identifier for the customer.
        user_api_key: User's API key for the LLM provider.
        name: Optional customer name for tracking.
        email: Optional customer email for tracking.

    Returns:
        A tuple containing the summary string and a metadata dictionary.

    Raises:
        InvalidAPIKeyError: If the API key is invalid.
        FileNotFoundError: If the prompt file is not found.
    """
    system_prompt = _load_system_prompt()
    formatted_explanations = _format_explanations(explanations)
    user_content = _create_user_content(formatted_explanations)

    posthog_properties = _create_posthog_properties(
        lecture_id, len(explanations), name, email
    )

    client = create_posthog_client(user_api_key, provider="openai")

    try:
        response = await client.responses.create(
            model=settings.summary_model,
            instructions=system_prompt,
            input=[{"role": "user", "content": user_content}],
            posthog_distinct_id=customer_identifier,
            posthog_trace_id=lecture_id,
            posthog_properties=posthog_properties,
        )

        summary = response.output_text
        metadata = _extract_metadata(response)

        if summary:
            return summary.strip(), metadata

        logging.warning("The AI model returned an empty summary.")
        return EMPTY_SUMMARY_ERROR_MESSAGE, metadata

    except Exception as e:
        if _is_authentication_error(e):
            logging.error(f"OpenAI authentication error (invalid API key): {e}")
            raise InvalidAPIKeyError(f"Invalid API key: {str(e)}") from e
        logging.error(
            f"An error occurred while calling the OpenAI API: {e}", exc_info=True
        )
        raise
