import asyncio
import logging
from typing import AsyncGenerator, List, Dict, Any

from app.utils.config import Settings
from app.utils.secret_manager import InvalidAPIKeyError
from app.utils.llm_utils import extract_text_from_response
from app.utils.posthog_client import get_openai_client
from app.utils.s3_utils import get_s3_client, download_image_as_base64


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
    resolved_references: List[Dict[str, Any]],
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
    Builds prompt with lecture context from RAG chunks, message history, and resolved references (images).
    Yields text chunks as they arrive.

    Args:
        query: Current user question
        context_chunks: RAG chunks retrieved from lecture
        resolved_references: References resolved into resources (images/OCR)
        lecture_id: Lecture ID for tracking
        chat_id: Chat ID for PostHog trace tracking
        user_id: User ID for tracking
        user_api_key: User's OpenAI API key
        model: Model to use for generation
        message_history: Optional list of previous messages (last 5 turns)
    """

    SYSTEM_PROMPT = """You are an expert AI University Tutor specializing in breaking down complex technical concepts into clear, digestible insights.

### YOUR GOAL
Explain the user's query based on the provided Lecture Slides. Your explanations must be simple, concise and effective.

### RESPONSE GUIDELINES
1.  **Top-Down Teaching:** Always start with a high-level summary of the "What" and "Why" before diving into the technical "How."
2.  **Adaptive Explanations (Use tools only when they add value):**
    - **Analogies:** Use them *only* if the concept is abstract or complex. If used, keep them brief and relevant.
    - **Visuals (Mermaid.js):** Use *only* if explaining a process, data flow, or logical hierarchy.
    - **Tables:** Use *only* for comparisons or distinct code breakdowns.
    - **Concrete Examples:** Mandatory for math, algorithms, or code logic.
3.  **Natural Flow:** Do not use generic headers like "The Analogy" or "The Big Picture" unless necessary. Use descriptive headers that match the content (e.g., "Analogy: The Hotel System").
4.  **Tone:** Smart 15-year-old. Concise and direct.

### RULES
- **Context is King:** Base your answer strictly on the `<lecture_context>`. Use general knowledge only to fill gaps or provide analogies.
- **Format for Scannability:** Always use Markdown. Structure your response with clear **Headings**, **Numbered Lists**, **Bullet Points**, and **Tables**. Avoid long paragraphs.
- **Be Concise:** Get straight to the point.
- **Latex:** Use LaTeX for all math formulas."""

    messages = [{"role": "developer", "content": SYSTEM_PROMPT}]

    # Build context from RAG chunks
    # 1. Formatting context chunks with XML tags for better boundary detection
    context_text = ""
    for chunk in context_chunks:
        context_text += f"""
    <slide id="{chunk['slide_number']}" chunk="{chunk['chunk_index']}">
    {chunk['text']}
    </slide>
    """

    # 2. Add the user message with explicit instruction on how to treat the context
    messages.append(
        {
            "role": "user",
            "content": f"""I am looking at the following lecture content. Use this as your primary source of truth:

    <lecture_context>
    {context_text}
    </lecture_context>

    Based on the context above (and any images provided), please answer my upcoming question.""",
        }
    )

    # Add message history directly to the list
    if message_history:
        # Append the last 5 turns directly as history
        # The current message_history list is assumed to be ordered oldest to newest.
        for msg in message_history:
            messages.append({"role": msg["role"], "content": msg["text"]})

    # 1. Add resolved references (images) as separate messages
    if resolved_references:
        s3_client = get_s3_client()
        try:
            for ref in resolved_references:
                if ref["type"] == "slide":
                    slide_num = ref["id"]
                    for res in ref["resources"]:
                        # Add image if path is available
                        if res.get("storage_path"):
                            img_base64 = download_image_as_base64(
                                s3_client, settings.s3_bucket_name, res["storage_path"]
                            )
                            if img_base64:
                                messages.append(
                                    {
                                        "role": "user",
                                        "content": [
                                            {
                                                "type": "image_url",
                                                "image_url": {
                                                    "url": f"data:image/png;base64,{img_base64}"
                                                },
                                            },
                                            {
                                                "type": "text",
                                                "text": f"Context: This is the visual slide (Slide {slide_num}) corresponding to the text provided earlier. Analyze the diagrams/code structure visually.",
                                            },
                                        ],
                                    }
                                )
        finally:
            s3_client.close()

    # 2. Add the user query text as the final message
    messages.append({"role": "user", "content": query})

    posthog_properties = _create_chat_posthog_properties(
        lecture_id, chat_id, len(context_chunks)
    )

    client = get_openai_client(user_api_key, base_url=base_url)

    # Log the prompt for debugging
    logging.info(f"--- CHAT PROMPT (lecture_id={lecture_id}, chat_id={chat_id}) ---")
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if isinstance(content, list):
            # Handle multi-modal content by summarizing image data
            log_content = []
            for item in content:
                if item.get("type") == "image_url":
                    log_content.append(
                        {"type": "image_url", "url": "[IMAGE_DATA_TRUNCATED]"}
                    )
                else:
                    log_content.append(item)
            logging.info(f"[{role}]: {log_content}")
        else:
            logging.info(f"[{role}]: {content}")
    logging.info("--- END CHAT PROMPT ---")

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

    # Log the prompt for debugging
    logging.info(
        f"--- TITLE GENERATION PROMPT (lecture_id={lecture_id}, chat_id={chat_id}) ---"
    )
    for msg in messages:
        logging.info(f"[{msg.get('role')}]: {msg.get('content')}")
    logging.info("--- END TITLE GENERATION PROMPT ---")

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
