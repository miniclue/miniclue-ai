import asyncio
import logging
from uuid import UUID
from typing import AsyncGenerator

import asyncpg

from app.services.chat import db_utils, rag_utils, llm_utils, query_rewriter
from app.utils.config import Settings
from app.utils.secret_manager import (
    get_user_api_key,
    SecretNotFoundError,
    InvalidAPIKeyError,
)

settings = Settings()


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
    conn = None
    try:
        conn = await asyncpg.connect(settings.postgres_dsn, statement_cache_size=0)

        # Verify lecture exists and user owns it
        if not await db_utils.verify_lecture_exists_and_ownership(
            conn, lecture_id, user_id
        ):
            raise ValueError(
                f"Lecture {lecture_id} not found or user {user_id} does not own it"
            )

        # Extract text from message parts
        query_text = ""
        for part in message:
            if part.get("type") == "text" and part.get("text"):
                query_text += part["text"] + " "

        query_text = query_text.strip()
        if not query_text:
            raise ValueError("Message must contain at least one text part")

        # Fetch user OpenAI API key from Secret Manager (required)
        try:
            user_api_key = get_user_api_key(str(user_id), provider="openai")
        except SecretNotFoundError:
            logging.error(f"API key not found for user {user_id}")
            raise InvalidAPIKeyError(
                "User API key not found. Please configure your API key in settings."
            )
        except Exception as e:
            logging.error(f"Failed to fetch user API key for {user_id}: {e}")
            raise InvalidAPIKeyError(f"Failed to access API key: {str(e)}")

        # Fetch message history once (up to 5 turns = 10 messages) for both query rewriting and final prompt
        # Fetch 11 messages to account for excluding the current user message, ensuring we have up to 5 turns after exclusion
        message_history = await db_utils.get_message_history(
            conn=conn, chat_id=chat_id, user_id=user_id, limit=11
        )

        # Exclude the most recent message if it's a user message (the current message being processed)
        # The current user message is saved to the database before processing, so we need to exclude it
        if message_history and message_history[-1].get("role") == "user":
            message_history = message_history[:-1]

        # Rewrite query using available history (up to last 3 turns) only if history exists
        rewritten_query = query_text
        if message_history:
            try:
                rewritten_query = await query_rewriter.rewrite_query(
                    current_question=query_text,
                    message_history=message_history,
                    user_api_key=user_api_key,
                    user_id=str(user_id),
                    lecture_id=str(lecture_id),
                )
            except Exception as e:
                logging.warning(f"Query rewriting failed, using original query: {e}")
                # Continue with original query if rewriting fails

        # Retrieve relevant chunks via RAG using rewritten query
        context_chunks = await rag_utils.retrieve_relevant_chunks(
            conn=conn,
            lecture_id=lecture_id,
            query_text=rewritten_query,
            user_api_key=user_api_key,
            user_id=user_id,
            top_k=settings.rag_top_k,
        )

        # Stream LLM response with message history
        try:
            async for chunk in llm_utils.stream_chat_response(
                query=query_text,
                context_chunks=context_chunks,
                message_history=message_history,
                lecture_id=str(lecture_id),
                user_id=str(user_id),
                user_api_key=user_api_key,
                model=model,
            ):
                yield chunk
        except asyncio.CancelledError:
            logging.warning(
                f"Chat request cancelled: lecture_id={lecture_id}, "
                f"chat_id={chat_id}, user_id={user_id}"
            )
            raise

    except asyncio.CancelledError:
        logging.warning(
            f"Chat request cancelled: lecture_id={lecture_id}, "
            f"chat_id={chat_id}, user_id={user_id}"
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
        if conn:
            await conn.close()
