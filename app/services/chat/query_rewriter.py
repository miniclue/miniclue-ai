import logging
from typing import List, Dict, Any, TYPE_CHECKING

from app.utils.config import Settings
from app.utils.secret_manager import InvalidAPIKeyError
from app.utils.llm_utils import extract_text_from_response
from app.utils.model_provider_mapping import get_provider_for_model
from app.utils.posthog_client import get_posthog_kwargs

if TYPE_CHECKING:
    from posthog.ai.openai import AsyncOpenAI

# Constants
HISTORY_TURNS_COUNT = 3
MESSAGES_PER_TURN = 2

# Initialize settings
settings = Settings()


def _is_authentication_error(error: Exception) -> bool:
    """Checks if the error is related to authentication/invalid API key."""
    error_str = str(error).lower()
    auth_indicators = ["authentication", "unauthorized", "invalid api key", "401"]
    return any(indicator in error_str for indicator in auth_indicators)


def _create_query_rewriter_posthog_properties(
    lecture_id: str,
    chat_id: str,
    history_turns: int,
) -> dict:
    """Creates PostHog properties dictionary for query rewriter tracking."""
    return {
        "service": "chat_query_rewriter",
        "lecture_id": lecture_id,
        "chat_id": chat_id,
        "history_turns": history_turns,
    }


async def rewrite_query(
    current_question: str,
    message_history: List[Dict[str, Any]],
    client: "AsyncOpenAI",
    user_id: str,
    lecture_id: str,
    chat_id: str,
) -> str:
    """
    Rewrite the user's query based on conversation history using Chat Completions API.
    Extracts last 3 turns (6 messages) from the provided history.

    Args:
        current_question: The user's current question
        message_history: List of message dicts with 'role' and 'text' keys (chronological order)
        client: PostHog-wrapped OpenAI client
        user_id: User ID for PostHog tracking
        lecture_id: Lecture ID for PostHog tracking
        chat_id: Chat ID for PostHog trace tracking

    Returns:
        Rewritten query string optimized for semantic search retrieval
    """

    REWRITING_SYSTEM_PROMPT = """You are an expert Query Rewriting Assistant for a Retrieval-Augmented Generation (RAG) system.
Your task is to take the current user question and the preceding conversation history, and rewrite the current question into a **clear, standalone, self-contained query** that is highly optimized for semantic search retrieval.

Instructions:
1.  **Resolve Co-references:** Replace vague terms like "it," "that," or "this" with the full entity name mentioned earlier in the history.
2.  **Be Comprehensive:** The rewritten query must stand on its own and make sense without needing the history.
3.  **Optimize for Retrieval:** Focus on keywords and concepts from the user's question and history.
4.  **Output Format:** Respond ONLY with the single, rewritten query string, and nothing else."""

    # Extract last 3 turns (6 messages) from history
    max_messages = HISTORY_TURNS_COUNT * MESSAGES_PER_TURN
    last_turns = (
        message_history[-max_messages:]
        if len(message_history) >= max_messages
        else message_history
    )

    # Build messages array for chat.completions API
    input_messages = []

    # Add last turns directly to the list
    for msg in last_turns:
        input_messages.append({"role": msg["role"], "content": msg["text"]})

    # Add the final message with the current question and explicit instruction
    input_messages.append(
        {
            "role": "user",
            "content": f"The final question to rewrite is: {current_question}\n\nRewritten Query:",
        }
    )

    history_turns = len(last_turns) // MESSAGES_PER_TURN if last_turns else 0
    posthog_properties = _create_query_rewriter_posthog_properties(
        lecture_id, chat_id, history_turns
    )

    messages = [{"role": "system", "content": REWRITING_SYSTEM_PROMPT}] + input_messages

    try:
        response = await client.chat.completions.create(
            model=settings.query_rewriter_model,
            messages=messages,
            **get_posthog_kwargs(
                user_id=user_id,
                trace_id=chat_id,
                properties={
                    "$ai_span_name": "chat_query_rewriter",
                    "$ai_provider": get_provider_for_model(
                        settings.query_rewriter_model
                    ),
                    **posthog_properties,
                },
            ),
        )

        rewritten_query = extract_text_from_response(response).strip()

        if not rewritten_query:
            logging.warning(
                "Query rewriter returned empty response, using original question"
            )
            return current_question

        return rewritten_query

    except Exception as e:
        if _is_authentication_error(e):
            logging.error(
                f"OpenAI authentication error in query rewriter: "
                f"user_id={user_id}, error={e}"
            )
            raise InvalidAPIKeyError(f"Invalid API key: {str(e)}") from e

        logging.warning(
            f"Query rewriting failed, using original question: "
            f"user_id={user_id}, error={e}"
        )
        # Fall back to original question if rewriting fails
        return current_question
