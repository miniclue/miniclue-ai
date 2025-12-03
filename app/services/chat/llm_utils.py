import asyncio
import logging
from typing import AsyncGenerator, List, Dict, Any

from app.utils.config import Settings
from app.utils.secret_manager import InvalidAPIKeyError
from app.utils.llm_utils import extract_text_from_response
from app.utils.posthog_client import get_openai_client

# Constants
TITLE_MAX_LENGTH = 80
TITLE_MODEL = "gpt-4.1-nano"
TITLE_MAX_TOKENS = 50
TITLE_TEMPERATURE = 0.7
ASSISTANT_MESSAGE_PREVIEW_LENGTH = 200

# Initialize settings
settings = Settings()


def _is_authentication_error(error: Exception) -> bool:
    """Checks if the error is related to authentication/invalid API key."""
    error_str = str(error).lower()
    auth_indicators = ["authentication", "unauthorized", "invalid api key", "401"]
    return any(indicator in error_str for indicator in auth_indicators)


def _extract_metadata(response) -> dict:
    """Extracts metadata from LLM response."""
    usage = None
    if hasattr(response, "usage") and response.usage:
        if hasattr(response.usage, "model_dump"):
            usage = response.usage.model_dump()
        else:
            usage = {
                "prompt_tokens": getattr(response.usage, "prompt_tokens", None),
                "completion_tokens": getattr(response.usage, "completion_tokens", None),
                "total_tokens": getattr(response.usage, "total_tokens", None),
            }
    return {
        "model": getattr(response, "model", ""),
        "usage": usage,
        "response_id": getattr(response, "id", ""),
    }


def _create_chat_posthog_properties(
    lecture_id: str,
    chat_id: str,
    context_chunks_count: int,
) -> dict:
    """Creates PostHog properties dictionary for chat tracking."""
    return {
        "lecture_id": lecture_id,
        "chat_id": chat_id,
        "context_chunks_count": context_chunks_count,
    }


def _create_title_posthog_properties(
    lecture_id: str,
    chat_id: str,
) -> dict:
    """Creates PostHog properties dictionary for title generation tracking."""
    return {
        "lecture_id": lecture_id,
        "chat_id": chat_id,
    }


async def stream_chat_response(
    query: str,
    context_chunks: List[Dict[str, Any]],
    lecture_id: str,
    chat_id: str,
    user_id: str,
    user_api_key: str,
    model: str,
    message_history: List[Dict[str, Any]] | None = None,
    base_url: str | None = None,
) -> AsyncGenerator[str, None]:
    """
    Stream chat response using OpenAI Chat Completions API streaming.
    Builds prompt with lecture context from RAG chunks and message history.
    Yields text chunks as they arrive.

    Args:
        query: Current user question
        context_chunks: RAG chunks retrieved from lecture
        lecture_id: Lecture ID for tracking
        chat_id: Chat ID for PostHog trace tracking
        user_id: User ID for tracking
        user_api_key: User's OpenAI API key
        model: Model to use for generation
        message_history: Optional list of previous messages (last 5 turns)
    """

    # Build context from RAG chunks
    context_text = "\n\n".join(
        [
            f"[Slide {chunk['slide_number']}, Chunk {chunk['chunk_index']}]\n{chunk['text']}"
            for chunk in context_chunks
        ]
    )

    # Build system prompt
    SYSTEM_PROMPT = f"""You are a helpful AI assistant explaining lecture materials.
1. **Source:** Always use the provided lecture context (RAG chunks) first. If the context is insufficient, use your general knowledge.
2. **Format:** Respond in **Markdown**. Use **bullet points** or numbered lists when explaining multiple points or steps for easy reading. Use **bold text** for key terms.
3. **Tone:** Be concise, clear, and academic.
4. **Context:** The following content is the lecture material you must use.

--- LECTURE CONTEXT ---
{context_text}
--- END LECTURE CONTEXT ---
"""

    # Build messages array for chat.completions API
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Add message history directly to the list
    if message_history:
        # Append the last 5 turns directly as history
        # The current message_history list is assumed to be ordered oldest to newest.
        for msg in message_history:
            messages.append({"role": msg["role"], "content": msg["text"]})

    # Add the current user query as the final message
    messages.append({"role": "user", "content": query})

    posthog_properties = _create_chat_posthog_properties(
        lecture_id, chat_id, len(context_chunks)
    )

    client = get_openai_client(user_api_key, base_url=base_url)

    try:
        stream = await client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
            posthog_distinct_id=user_id,
            posthog_trace_id=chat_id,
            posthog_properties={
                "$ai_span_name": "chat_response",
                **posthog_properties,
            },
        )

        async for chunk in stream:
            # Handle streaming events from chat.completions API
            if chunk.choices and len(chunk.choices) > 0:
                delta = chunk.choices[0].delta
                if hasattr(delta, "content") and delta.content:
                    yield delta.content

    except asyncio.CancelledError:
        logging.warning(
            f"Stream cancelled for chat: lecture_id={lecture_id}, user_id={user_id}, model={model}"
        )
        # Stream will be cleaned up automatically when cancelled
        # Re-raise to allow FastAPI to handle the cancellation properly
        raise
    except Exception as e:
        if _is_authentication_error(e):
            logging.error(
                f"OpenAI authentication error (invalid API key): "
                f"lecture_id={lecture_id}, user_id={user_id}, model={model}, error={e}"
            )
            raise InvalidAPIKeyError(f"Invalid API key: {str(e)}") from e
        logging.error(
            f"An error occurred while calling the OpenAI API for chat: "
            f"lecture_id={lecture_id}, user_id={user_id}, model={model}, error={e}",
            exc_info=True,
        )
        raise


async def generate_chat_title(
    user_message: str,
    assistant_message: str,
    user_api_key: str,
    user_id: str,
    lecture_id: str,
    chat_id: str,
) -> tuple[str, dict]:
    """
    Generate a concise title for a chat based on the first user message and assistant response.
    Uses a lightweight prompt to generate a title (max 80 characters).

    Args:
        user_message: The first user message text
        assistant_message: The first assistant response text
        user_api_key: User's OpenAI API key
        user_id: User ID for tracking
        lecture_id: Lecture ID for tracking
        chat_id: Chat ID for PostHog trace tracking

    Returns:
        Tuple of (title, usage_metadata)
    """

    SYSTEM_PROMPT = f"""Generate a concise title (maximum {TITLE_MAX_LENGTH} characters) that summarizes the conversation between the user's question and the assistant's response. The title should capture the main topic or question being discussed. Be clear and descriptive. Do not include quotes, colons, or special formatting. Return only the title text."""

    # Combine user question and assistant response for context
    conversation_context = f"User: {user_message}\n\nAssistant: {assistant_message[:ASSISTANT_MESSAGE_PREVIEW_LENGTH]}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": conversation_context},
    ]

    posthog_properties = _create_title_posthog_properties(lecture_id, chat_id)

    # Always use OpenAI default base_url for title generation
    client = get_openai_client(user_api_key)

    try:
        response = await client.chat.completions.create(
            model=TITLE_MODEL,
            messages=messages,
            max_tokens=TITLE_MAX_TOKENS,
            temperature=TITLE_TEMPERATURE,
            posthog_distinct_id=user_id,
            posthog_trace_id=chat_id,
            posthog_properties={
                "$ai_span_name": "chat_title",
                **posthog_properties,
            },
        )

        title = extract_text_from_response(response).strip()
        # Ensure title doesn't exceed max length
        if len(title) > TITLE_MAX_LENGTH:
            title = title[: TITLE_MAX_LENGTH - 3] + "..."

        usage_metadata = _extract_metadata(response)

        return title, usage_metadata

    except Exception as e:
        if _is_authentication_error(e):
            logging.error(
                f"OpenAI authentication error (invalid API key) for title generation: "
                f"lecture_id={lecture_id}, user_id={user_id}, error={e}"
            )
            raise InvalidAPIKeyError(f"Invalid API key: {str(e)}") from e
        logging.error(
            f"An error occurred while calling the OpenAI API for title generation: "
            f"lecture_id={lecture_id}, user_id={user_id}, error={e}",
            exc_info=True,
        )
        raise
