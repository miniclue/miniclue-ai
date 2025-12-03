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
from app.utils.model_provider_mapping import get_provider_for_model
from app.utils.posthog_client import get_base_url_for_provider

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

        # Determine provider from model ID for streaming response
        provider = get_provider_for_model(model)
        if provider is None:
            raise ValueError(f"Unknown model: {model}")

        # Fetch OpenAI API key for query rewriting and RAG (always use OpenAI)
        try:
            openai_api_key = get_user_api_key(str(user_id), provider="openai")
        except SecretNotFoundError:
            logging.error(f"OpenAI API key not found for user {user_id}")
            raise InvalidAPIKeyError(
                "User OpenAI API key not found. Please configure your API key in settings."
            )
        except Exception as e:
            logging.error(f"Failed to fetch OpenAI API key for {user_id}: {e}")
            raise InvalidAPIKeyError(f"Failed to access OpenAI API key: {str(e)}")

        # Fetch user API key for the determined provider from Secret Manager (for streaming response)
        try:
            user_api_key = get_user_api_key(str(user_id), provider=provider)
        except SecretNotFoundError:
            logging.error(f"API key not found for user {user_id}, provider {provider}")
            raise InvalidAPIKeyError(
                f"User API key not found for {provider}. Please configure your API key in settings."
            )
        except Exception as e:
            logging.error(
                f"Failed to fetch user API key for {user_id}, provider {provider}: {e}"
            )
            raise InvalidAPIKeyError(f"Failed to access API key: {str(e)}")

        # Get base URL for the provider (only for streaming response)
        base_url = get_base_url_for_provider(provider)

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
        # Use OpenAI API key for query rewriting
        rewritten_query = query_text
        if message_history:
            try:
                rewritten_query = await query_rewriter.rewrite_query(
                    current_question=query_text,
                    message_history=message_history,
                    user_api_key=openai_api_key,
                    user_id=str(user_id),
                    lecture_id=str(lecture_id),
                    chat_id=str(chat_id),
                )
            except Exception as e:
                logging.warning(f"Query rewriting failed, using original query: {e}")
                # Continue with original query if rewriting fails

        # Retrieve relevant chunks via RAG using rewritten query
        # Use OpenAI API key for RAG embeddings
        context_chunks = await rag_utils.retrieve_relevant_chunks(
            conn=conn,
            lecture_id=lecture_id,
            query_text=rewritten_query,
            user_api_key=openai_api_key,
            user_id=user_id,
            chat_id=str(chat_id),
            top_k=settings.rag_top_k,
        )

        # Stream LLM response with message history
        try:
            async for chunk in llm_utils.stream_chat_response(
                query=query_text,
                context_chunks=context_chunks,
                message_history=message_history,
                lecture_id=str(lecture_id),
                chat_id=str(chat_id),
                user_id=str(user_id),
                user_api_key=user_api_key,
                model=model,
                base_url=base_url,
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

        # Extract text from user message parts
        user_message_text = ""
        for part in user_message:
            if part.get("type") == "text" and part.get("text"):
                user_message_text += part["text"] + " "

        user_message_text = user_message_text.strip()
        if not user_message_text:
            raise ValueError("User message must contain at least one text part")

        # Extract text from assistant message parts
        assistant_message_text = ""
        for part in assistant_message:
            if part.get("type") == "text" and part.get("text"):
                assistant_message_text += part["text"] + " "

        assistant_message_text = assistant_message_text.strip()
        if not assistant_message_text:
            raise ValueError("Assistant message must contain at least one text part")

        # For title generation, use OpenAI provider (default)
        provider = "openai"
        try:
            user_api_key = get_user_api_key(str(user_id), provider=provider)
        except SecretNotFoundError:
            logging.error(f"API key not found for user {user_id}, provider {provider}")
            raise InvalidAPIKeyError(
                f"User API key not found for {provider}. Please configure your API key in settings."
            )
        except Exception as e:
            logging.error(
                f"Failed to fetch user API key for {user_id}, provider {provider}: {e}"
            )
            raise InvalidAPIKeyError(f"Failed to access API key: {str(e)}")

        # Generate title via LLM (always use OpenAI, no custom base_url)
        title, _ = await llm_utils.generate_chat_title(
            user_message=user_message_text,
            assistant_message=assistant_message_text,
            user_api_key=user_api_key,
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
        if conn:
            await conn.close()
