import asyncio
import logging
from uuid import UUID
from typing import AsyncGenerator, TYPE_CHECKING

import asyncpg

if TYPE_CHECKING:
    from posthog.ai.openai import AsyncOpenAI
    from google.genai import Client

from app.services.chat import db_utils, rag_utils, llm_utils, query_rewriter
from app.utils.config import Settings
from app.utils.llm_utils import get_llm_context


settings = Settings()


async def _parse_message_parts(
    conn: asyncpg.Connection, lecture_id: UUID, message: list[dict]
) -> tuple[str, list[dict]]:
    """
    Extracts text and resolves references from message parts.
    Returns (query_text, resolved_references).
    """
    query_text = ""
    resolved_references = []
    marker_map = {}  # Map of REF_X to [Slide X]
    seen_slide_ids = set()

    for part in message:
        part_type = part.get("type")
        if part_type == "text" and part.get("text"):
            query_text += part["text"]
        elif part_type == "data-reference" and part.get("data"):
            data = part["data"]
            ref = data.get("reference")
            if not ref:
                continue

            # Collect reference marker for replacement
            ref_marker = data.get("text") or ref.get("metadata", {}).get("ref")

            if ref.get("type") == "slide":
                try:
                    slide_number_id = ref.get("id")
                    if not slide_number_id:
                        continue

                    slide_number = int(slide_number_id)
                    if ref_marker:
                        marker_map[ref_marker] = f"[Slide {slide_number}]"

                    if slide_number in seen_slide_ids:
                        continue

                    seen_slide_ids.add(slide_number)

                    resources = await db_utils.get_slide_resources(
                        conn, lecture_id, slide_number
                    )
                    if resources:
                        resolved_references.append(
                            {
                                "type": "slide",
                                "id": slide_number,
                                "resources": resources,
                            }
                        )
                except (ValueError, TypeError):
                    logging.warning(
                        f"Invalid slide number in reference: {ref.get('id')}"
                    )

    # Replace reference markers in query text with descriptive names
    for marker, replacement in marker_map.items():
        query_text = query_text.replace(marker, replacement)

    return query_text.strip(), resolved_references


async def _get_api_context(
    user_id: UUID, model: str
) -> tuple["AsyncOpenAI", "Client", "AsyncOpenAI"]:
    """
    Determines provider and fetches necessary API clients.
    Returns (chat_client, chat_provider, embedding_client, rewriter_client).
    """

    # 1. Fetch Embedding API client
    embedding_client, _ = await get_llm_context(
        user_id, settings.embedding_model, is_embedding=True
    )

    # 2. Fetch Rewriter API client
    rewriter_client, _ = await get_llm_context(user_id, settings.query_rewriter_model)

    # 3. Fetch Provider-specific API client (for streaming)
    chat_client, _ = await get_llm_context(user_id, model)

    return chat_client, embedding_client, rewriter_client


async def _get_processed_history(
    conn: asyncpg.Connection, chat_id: UUID, user_id: UUID
) -> list[dict]:
    """
    Fetches and processes message history, excluding the current user message if present.
    """
    # Fetch 11 messages to account for excluding the current user message, ensuring we have up to 5 turns after exclusion
    message_history = await db_utils.get_message_history(
        conn=conn, chat_id=chat_id, user_id=user_id, limit=11
    )

    # Exclude the most recent message if it's a user message (the current message being processed)
    if message_history and message_history[-1].get("role") == "user":
        message_history = message_history[:-1]

    return message_history


async def process_chat_request(
    lecture_id: UUID,
    chat_id: UUID,
    user_id: UUID,
    message: list[dict],
    model: str,
) -> AsyncGenerator[str, None]:
    """
    Main orchestration for chat requests.
    Verifies lecture exists and user owns it.
    Generates query embedding, retrieves relevant chunks via RAG.
    Streams LLM response.
    Returns async generator of text chunks.
    """
    conn = await asyncpg.connect(settings.postgres_dsn, statement_cache_size=0)
    try:
        # 1. Verify lecture exists and user owns it
        if not await db_utils.verify_lecture_exists_and_ownership(
            conn, lecture_id, user_id
        ):
            raise ValueError(
                f"Lecture {lecture_id} not found or user {user_id} does not own it"
            )

        # 2. Parse message parts, resolve references and get resources
        query_text, resolved_references = await _parse_message_parts(
            conn, lecture_id, message
        )
        if not query_text:
            raise ValueError("Message must contain at least one text part")

        # 3. Get API context (clients)
        (
            chat_client,
            embedding_client,
            rewriter_client,
        ) = await _get_api_context(user_id, model)

        # 4. Get message history
        message_history = await _get_processed_history(conn, chat_id, user_id)

        # 5. Rewrite query using available history
        rewritten_query = query_text
        if message_history:
            try:
                rewritten_query = await query_rewriter.rewrite_query(
                    current_question=query_text,
                    message_history=message_history,
                    client=rewriter_client,
                    user_id=str(user_id),
                    lecture_id=str(lecture_id),
                    chat_id=str(chat_id),
                )
            except Exception as e:
                logging.warning(f"Query rewriting failed, using original query: {e}")

        # 6. Retrieve relevant chunks via RAG using rewritten query
        context_chunks = await rag_utils.retrieve_relevant_chunks(
            conn=conn,
            lecture_id=lecture_id,
            query_text=rewritten_query,
            client=embedding_client,
            user_id=str(user_id),
            chat_id=str(chat_id),
            top_k=settings.rag_top_k,
        )

        # 7. Stream LLM response
        async for chunk in llm_utils.stream_chat_response(
            query=query_text,
            context_chunks=context_chunks,
            resolved_references=resolved_references,
            message_history=message_history,
            lecture_id=str(lecture_id),
            chat_id=str(chat_id),
            user_id=str(user_id),
            client=chat_client,
            model=model,
        ):
            yield chunk

    except asyncio.CancelledError:
        logging.warning(
            f"Chat request cancelled: lecture_id={lecture_id}, chat_id={chat_id}, user_id={user_id}"
        )
        raise
    except Exception as e:
        logging.error(
            f"Error processing chat request: lecture_id={lecture_id}, "
            f"chat_id={chat_id}, user_id={user_id}, model={model}, error={e}",
            exc_info=True,
        )
        raise
    finally:
        await conn.close()


def _extract_text_from_message(message: list[dict]) -> str:
    """
    Extracts plain text from message parts.
    """
    text = ""
    for part in message:
        if part.get("type") == "text" and part.get("text"):
            text += part["text"] + " "
    return text.strip()


async def process_title_generation(
    lecture_id: UUID,
    chat_id: UUID,
    user_id: UUID,
    user_message: list[dict],
    assistant_message: list[dict],
) -> str:
    """
    Orchestrates title generation for a chat based on the first user message and assistant response.
    Verifies lecture ownership, extracts message text, and generates title via LLM.

    Args:
        lecture_id: Lecture ID
        chat_id: Chat ID
        user_id: User ID
        user_message: List of user message parts (dicts with type and text)
        assistant_message: List of assistant message parts (dicts with type and text)

    Returns:
        Generated title string
    """
    conn = await asyncpg.connect(settings.postgres_dsn, statement_cache_size=0)
    try:
        # 1. Verify lecture exists and user owns it
        if not await db_utils.verify_lecture_exists_and_ownership(
            conn, lecture_id, user_id
        ):
            raise ValueError(
                f"Lecture {lecture_id} not found or user {user_id} does not own it"
            )

        # 2. Extract text from messages
        user_text = _extract_text_from_message(user_message)
        if not user_text:
            raise ValueError("User message must contain at least one text part")

        assistant_text = _extract_text_from_message(assistant_message)
        if not assistant_text:
            raise ValueError("Assistant message must contain at least one text part")

        # 3. Fetch client for title generation
        client, _ = await get_llm_context(user_id, settings.title_model)

        # 4. Generate title via LLM
        title, _ = await llm_utils.generate_chat_title(
            user_message=user_text,
            assistant_message=assistant_text,
            client=client,
            user_id=str(user_id),
            lecture_id=str(lecture_id),
            chat_id=str(chat_id),
        )

        return title

    except Exception as e:
        logging.error(
            f"Error processing title generation: lecture_id={lecture_id}, "
            f"chat_id={chat_id}, user_id={user_id}, error={e}",
            exc_info=True,
        )
        raise
    finally:
        await conn.close()
