import asyncio
import logging
from typing import AsyncGenerator, List, Dict, Any

from app.utils.config import Settings
from app.utils.posthog_client import create_posthog_client
from app.utils.secret_manager import InvalidAPIKeyError

settings = Settings()


async def stream_chat_response(
    query: str,
    context_chunks: List[Dict[str, Any]],
    lecture_id: str,
    user_id: str,
    user_api_key: str,
    model: str,
    message_history: List[Dict[str, Any]] | None = None,
) -> AsyncGenerator[str, None]:
    """
    Stream chat response using OpenAI streaming API.
    Builds prompt with lecture context from RAG chunks and message history.
    Yields text chunks as they arrive.

    Args:
        query: Current user question
        context_chunks: RAG chunks retrieved from lecture
        lecture_id: Lecture ID for tracking
        user_id: User ID for tracking
        user_api_key: User's OpenAI API key
        model: Model to use for generation
        message_history: Optional list of previous messages (last 5 turns)
    """
    if settings.mock_llm_calls:
        # Mock streaming response
        mock_response = (
            f"Mock response for query: {query}\n\nContext chunks: {len(context_chunks)}"
        )
        if message_history:
            mock_response += f"\nMessage history: {len(message_history)} messages"
        for char in mock_response:
            yield char
        return

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

    messages_for_api = [
        {"role": "system", "content": SYSTEM_PROMPT},
    ]

    # Add message history directly to the list
    if message_history:
        # Append the last 5 turns directly as history
        # The current message_history list is assumed to be ordered oldest to newest.
        for msg in message_history:
            messages_for_api.append({"role": msg["role"], "content": msg["text"]})

    # Add the current user query as the final message
    messages_for_api.append({"role": "user", "content": query})

    # Create client with user's API key
    client = create_posthog_client(user_api_key, provider="openai")

    stream = None
    try:
        stream = await client.chat.completions.create(
            model=model,
            messages=messages_for_api,
            stream=True,
            posthog_distinct_id=user_id,
            posthog_trace_id=lecture_id,
            posthog_properties={
                "service": "chat",
                "lecture_id": lecture_id,
                "context_chunks_count": len(context_chunks),
            },
        )

        async for chunk in stream:
            if chunk.choices and len(chunk.choices) > 0:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content

    except asyncio.CancelledError:
        logging.warning(
            f"Stream cancelled for chat: lecture_id={lecture_id}, user_id={user_id}, model={model}"
        )
        # Stream will be cleaned up automatically when cancelled
        # Re-raise to allow FastAPI to handle the cancellation properly
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
