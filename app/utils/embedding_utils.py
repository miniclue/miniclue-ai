import logging
import json
import time
from typing import List, Dict, Any, Tuple
from app.utils.config import Settings
from app.utils.secret_manager import InvalidAPIKeyError
from app.utils.llm_utils import is_authentication_error
from app.utils.posthog_client import get_posthog_client
from app.utils.model_provider_mapping import get_provider_for_model
from google.genai import types

settings = Settings()


def _create_posthog_properties(
    lecture_id: str | None, chat_id: str | None, texts_count: int
) -> dict:
    """Creates PostHog properties dictionary for tracking."""
    properties = {
        "texts_count": texts_count,
    }
    if lecture_id:
        properties["lecture_id"] = lecture_id
    if chat_id:
        properties["chat_id"] = chat_id
    return properties


def _capture_posthog_event(
    *,
    user_id: str,
    trace_id: str | None,
    span_name: str,
    texts: List[str],
    response: Any,
    latency: float,
    task_type: str,
    posthog_properties: dict,
) -> None:
    """Captures PostHog AI generation event for embeddings."""
    posthog_client = get_posthog_client()
    if not posthog_client:
        return

    try:
        usage = getattr(response, "usage", None)
        # Gemini responses might use prompt_token_count
        input_tokens = (
            getattr(usage, "prompt_token_count", None)
            or getattr(usage, "prompt_tokens", None)
            if usage
            else None
        )

        posthog_client.capture(
            distinct_id=user_id,
            event="$ai_embedding",
            properties={
                "$ai_trace_id": trace_id,
                "$ai_span_name": span_name,
                "$ai_model": settings.embedding_model,
                "$ai_provider": get_provider_for_model(settings.embedding_model),
                "$ai_input": texts,
                "$ai_input_tokens": input_tokens,
                "$ai_latency": latency,
                "task_type": task_type,
                **posthog_properties,
            },
        )
    except Exception as e:
        logging.warning(f"Failed to capture PostHog event: {e}")


def generate_embeddings(
    texts: List[str],
    lecture_id: str | None = None,
    chat_id: str | None = None,
    *,
    user_id: str,
    client: Any,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Generate embedding vectors for a batch of text chunks.

    Args:
        texts: List of text strings to generate embeddings for.
        lecture_id: Optional unique identifier for the lecture.
        chat_id: Optional unique identifier for the chat.
        user_id: Unique identifier for the user.
        client: Client for generating embeddings (AsyncOpenAI or AsyncClient).

    Returns:
        A tuple containing a list of embedding results (with vector and metadata)
        and a common metadata dictionary.

    Raises:
        InvalidAPIKeyError: If the API key is invalid.
    """
    if not texts:
        return [], {}

    trace_id = lecture_id or chat_id
    span_name = "lecture_embedding" if lecture_id else "chat_embedding"
    task_type = "RETRIEVAL_DOCUMENT" if lecture_id else "RETRIEVAL_QUERY"

    posthog_properties = _create_posthog_properties(lecture_id, chat_id, len(texts))

    try:
        start_time = time.time()
        response = client.models.embed_content(
            model=settings.embedding_model,
            contents=texts,
            config=types.EmbedContentConfig(
                output_dimensionality=768, task_type=task_type
            ),
        )
        latency = time.time() - start_time

        results: List[Dict[str, Any]] = []
        data_list = getattr(response, "embeddings", [])

        for data_item in data_list:
            embedding = getattr(data_item, "values", None)
            vector_str = json.dumps(embedding)
            # Store an empty object for per-item metadata to avoid redundancy
            results.append({"vector": vector_str, "metadata": json.dumps({})})

        _capture_posthog_event(
            user_id=user_id,
            trace_id=trace_id,
            span_name=span_name,
            texts=texts,
            response=response,
            latency=latency,
            task_type=task_type,
            posthog_properties=posthog_properties,
        )

        common_metadata = {
            "model": settings.embedding_model,
            "task_type": task_type,
        }

        return results, common_metadata
    except Exception as e:
        if is_authentication_error(e):
            logging.error(f"Authentication error (invalid API key): {e}")
            raise InvalidAPIKeyError(f"Invalid API key: {str(e)}") from e
        logging.error(
            f"An error occurred while calling the embedding API: {e}",
            exc_info=True,
        )
        raise
