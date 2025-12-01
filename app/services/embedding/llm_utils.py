import json
import logging
from typing import List, Dict, Any, Tuple
import litellm


from app.utils.config import Settings
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
    # Handle both dict and object responses from LiteLLM
    if isinstance(response, dict):
        model = response.get("model", "")
        usage = response.get("usage", {})
        if hasattr(usage, "model_dump"):
            usage = usage.model_dump()
    else:
        model = getattr(response, "model", "")
        usage_obj = getattr(response, "usage", None)
        usage = (
            usage_obj.model_dump()
            if usage_obj and hasattr(usage_obj, "model_dump")
            else {}
        )
    return {
        "model": model,
        "usage": usage,
    }


def _is_authentication_error(error: Exception) -> bool:
    """Checks if the error is related to authentication/invalid API key."""
    error_str = str(error).lower()
    auth_indicators = ["authentication", "unauthorized", "invalid api key", "401"]
    return any(indicator in error_str for indicator in auth_indicators)


async def generate_embeddings(
    texts: List[str], lecture_or_chat_id: str, user_id: str, user_api_key: str
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Generate embedding vectors for a batch of text chunks.

    Args:
        texts: List of text strings to generate embeddings for.
        lecture_or_chat_id: Unique identifier for the lecture or chat.
        user_id: Unique identifier for the user.
        user_api_key: User's API key for the LLM provider.

    Returns:
        A tuple containing a list of embedding results (with vector and metadata)
        and a common metadata dictionary.

    Raises:
        InvalidAPIKeyError: If the API key is invalid.
    """
    if not texts:
        return [], {}

    posthog_properties = _create_posthog_properties(lecture_or_chat_id, len(texts))

    litellm.success_callback = ["posthog"]

    try:
        response = await litellm.aembedding(
            model=settings.embedding_model,
            input=texts,
            api_key=user_api_key,
            metadata={
                "user_id": user_id,
                "$ai_trace_id": lecture_or_chat_id,
                **posthog_properties,
            },
        )

        common_metadata = _extract_metadata(response)
        results: List[Dict[str, Any]] = []
        # Handle both dict and object responses from LiteLLM
        if isinstance(response, dict):
            data_list = response.get("data", [])
        else:
            data_list = getattr(response, "data", [])

        for data_item in data_list:
            # Handle both dict and object access patterns
            if isinstance(data_item, dict):
                embedding = data_item.get("embedding", [])
            else:
                embedding = getattr(data_item, "embedding", [])
            vector_str = json.dumps(embedding)
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
