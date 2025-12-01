import json
import logging
from typing import List, Dict, Any, Tuple

from app.utils.config import Settings
from app.utils.posthog_client import create_posthog_client
from app.utils.secret_manager import InvalidAPIKeyError

# Initialize settings
settings = Settings()


def _create_posthog_properties(lecture_id: str, texts_count: int) -> dict:
    """Creates PostHog properties dictionary for tracking."""
    return {
        "service": "embedding",
        "lecture_id": lecture_id,
        "texts_count": texts_count,
    }


def _extract_metadata(response) -> Dict[str, Any]:
    """Extracts metadata from embeddings response."""
    return {
        "model": response.model,
        "usage": response.usage.model_dump(),
    }


def _is_authentication_error(error: Exception) -> bool:
    """Checks if the error is related to authentication/invalid API key."""
    error_str = str(error).lower()
    auth_indicators = ["authentication", "unauthorized", "invalid api key", "401"]
    return any(indicator in error_str for indicator in auth_indicators)


async def generate_embeddings(
    texts: List[str], lecture_id: str, customer_identifier: str, user_api_key: str
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Generate embedding vectors for a batch of text chunks.

    Args:
        texts: List of text strings to generate embeddings for.
        lecture_id: Unique identifier for the lecture.
        customer_identifier: Unique identifier for the customer.
        user_api_key: User's API key for the LLM provider.

    Returns:
        A tuple containing a list of embedding results (with vector and metadata)
        and a common metadata dictionary.

    Raises:
        InvalidAPIKeyError: If the API key is invalid.
    """
    if not texts:
        return [], {}

    posthog_properties = _create_posthog_properties(lecture_id, len(texts))
    client = create_posthog_client(user_api_key, provider="openai")

    try:
        response = await client.embeddings.create(
            model=settings.embedding_model,
            input=texts,
            posthog_distinct_id=customer_identifier,
            posthog_trace_id=lecture_id,
            posthog_properties=posthog_properties,
        )

        common_metadata = _extract_metadata(response)
        results: List[Dict[str, Any]] = []
        for data in response.data:
            vector_str = json.dumps(data.embedding)
            # Store an empty object for per-item metadata to avoid redundancy
            results.append({"vector": vector_str, "metadata": json.dumps({})})
        return results, common_metadata
    except Exception as e:
        if _is_authentication_error(e):
            logging.error(f"OpenAI authentication error (invalid API key): {e}")
            raise InvalidAPIKeyError(f"Invalid API key: {str(e)}") from e
        logging.error(
            f"An error occurred while calling the OpenAI API for embeddings: {e}",
            exc_info=True,
        )
        raise
