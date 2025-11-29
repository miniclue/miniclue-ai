import logging
from typing import List, Dict, Any

from app.utils.config import Settings
from app.utils.posthog_client import create_posthog_client
from app.utils.secret_manager import InvalidAPIKeyError

settings = Settings()


async def rewrite_query(
    current_question: str,
    message_history: List[Dict[str, Any]],
    user_api_key: str,
    user_id: str,
    lecture_id: str,
) -> str:
    """
    Rewrite the user's query based on conversation history using gpt-5-nano.
    Extracts last 3 turns (6 messages) from the provided history.

    Args:
        current_question: The user's current question
        message_history: List of message dicts with 'role' and 'text' keys (chronological order)
        user_api_key: User's OpenAI API key
        user_id: User ID for PostHog tracking
        lecture_id: Lecture ID for PostHog trace tracking

    Returns:
        Rewritten query string optimized for semantic search retrieval
    """

    # Build rewriting prompt
    # New system prompt variable
    REWRITING_SYSTEM_PROMPT = """You are an expert Query Rewriting Assistant for a Retrieval-Augmented Generation (RAG) system.
    Your task is to take the current user question and the preceding conversation history, and rewrite the current question into a **clear, standalone, self-contained query** that is highly optimized for semantic search retrieval.

    Instructions:
    1.  **Resolve Co-references:** Replace vague terms like "it," "that," or "this" with the full entity name mentioned earlier in the history.
    2.  **Be Comprehensive:** The rewritten query must stand on its own and make sense without needing the history.
    3.  **Optimize for Retrieval:** Focus on keywords and concepts from the user's question and history.
    4.  **Output Format:** Respond ONLY with the single, rewritten query string, and nothing else."""

    # Start with the instruction-based System Prompt
    messages_for_api = [
        {"role": "system", "content": REWRITING_SYSTEM_PROMPT},
    ]

    # Add last 3 turns (6 messages) directly to the list
    # The loop structure is sound, but we will simplify how we build the list.
    last_3_turns = (
        message_history[-6:] if len(message_history) >= 6 else message_history
    )

    for msg in last_3_turns:
        # Note: The 'role' from the input dict matches the expected OpenAI role
        messages_for_api.append({"role": msg["role"], "content": msg["text"]})

    # Add the final message with the current question and the explicit instruction for the LLM
    # The model will see the history, then the new question, and then the prompt asks for the rewrite.
    messages_for_api.append(
        {
            "role": "user",
            "content": f"The final question to rewrite is: {current_question}\n\nRewritten Query:",
        }
    )

    if settings.mock_llm_calls:
        # Mock rewritten query
        return current_question

    # Create client with user's API key
    client = create_posthog_client(user_api_key, provider="openai")

    try:
        response = await client.chat.completions.create(
            model="gpt-4.1-nano",
            messages=messages_for_api,
            posthog_distinct_id=user_id,
            posthog_trace_id=lecture_id,
            posthog_properties={
                "service": "chat_query_rewriter",
                "history_turns": len(last_3_turns) // 2 if last_3_turns else 0,
            },
        )

        rewritten_query = response.choices[0].message.content.strip()

        if not rewritten_query:
            logging.warning(
                "Query rewriter returned empty response, using original question"
            )
            return current_question

        return rewritten_query

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
