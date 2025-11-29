import logging
from uuid import UUID
from typing import AsyncGenerator

import asyncpg

from app.services.chat import db_utils, rag_utils, llm_utils
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

        # Retrieve relevant chunks via RAG
        context_chunks = await rag_utils.retrieve_relevant_chunks(
            conn=conn,
            lecture_id=lecture_id,
            query_text=query_text,
            user_api_key=user_api_key,
            user_id=user_id,
            top_k=settings.rag_top_k,
        )

        logging.info(
            f"Retrieved {len(context_chunks)} context chunks for lecture {lecture_id}"
        )

        # Stream LLM response
        async for chunk in llm_utils.stream_chat_response(
            query=query_text,
            context_chunks=context_chunks,
            lecture_id=str(lecture_id),
            user_id=str(user_id),
            user_api_key=user_api_key,
            model=model,
        ):
            yield chunk

    except Exception as e:
        logging.error(f"Error processing chat request: {e}", exc_info=True)
        raise
    finally:
        if conn:
            await conn.close()
