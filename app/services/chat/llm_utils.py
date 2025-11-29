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
) -> AsyncGenerator[str, None]:
    """
    Stream chat response using OpenAI streaming API.
    Builds prompt with lecture context from RAG chunks.
    Yields text chunks as they arrive.
    """
    if settings.mock_llm_calls:
        # Mock streaming response
        mock_response = (
            f"Mock response for query: {query}\n\nContext chunks: {len(context_chunks)}"
        )
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
    system_prompt = """You are a helpful AI assistant that answers questions about lecture materials. Use the provided context from the lecture slides to answer the user's question accurately. Use your own knowledge to answer the question if it is not provided in the context. Be concise and clear in your responses."""

    # Build user message
    user_message = f"""Context from lecture slides:

{context_text}

---

User question: {query}

Please answer the user's question based on the context above."""

    # Create client with user's API key
    client = create_posthog_client(user_api_key, provider="openai")

    try:
        stream = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
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
        logging.error(
            f"An error occurred while calling the OpenAI API for chat: {e}",
            exc_info=True,
        )
        raise
