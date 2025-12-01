import logging
import asyncpg
from app.schemas.summary import SummaryPayload
from app.services.summary import db_utils, llm_utils
from app.utils.config import Settings
from app.utils.secret_manager import (
    get_user_api_key,
    SecretNotFoundError,
    InvalidAPIKeyError,
)

settings = Settings()


async def process_summary_job(payload: SummaryPayload):
    """
    Orchestrates the entire process of generating a summary for a lecture.
    """
    lecture_id = payload.lecture_id

    conn = None
    try:
        conn = await asyncpg.connect(settings.postgres_dsn, statement_cache_size=0)

        # 1. Verify the lecture exists and is in a processable state
        if not await db_utils.verify_lecture_exists(conn, lecture_id):
            logging.warning(
                f"Lecture {lecture_id} not found or is in a terminal state."
            )
            return

        # 2. Check if summary already exists to ensure idempotency
        if await db_utils.check_summary_exists(conn, lecture_id):
            return

        # 3. Gather all slide explanations
        explanations = await db_utils.get_all_explanations(conn, lecture_id)
        if not explanations:
            logging.warning(
                f"[{lecture_id}]: No explanations found. Cannot generate summary."
            )
            # Optionally, you could set the lecture to a failed state here.
            return

        # 4. Set status to 'summarising' to indicate work is starting
        await conn.execute(
            "UPDATE lectures SET status = 'summarising' WHERE id = $1", lecture_id
        )

        # 5. Fetch user OpenAI API key from Secret Manager (required)
        try:
            user_api_key = get_user_api_key(
                payload.customer_identifier, provider="openai"
            )
        except SecretNotFoundError:
            logging.error(f"API key not found for user {payload.customer_identifier}")
            raise InvalidAPIKeyError(
                "User API key not found. Please configure your API key in settings."
            )
        except Exception as e:
            logging.error(
                f"Failed to fetch user API key for {payload.customer_identifier}: {e}"
            )
            raise InvalidAPIKeyError(f"Failed to access API key: {str(e)}")

        # 6. Call the AI model to synthesize the explanations into a summary
        summary_content, metadata = await llm_utils.generate_summary(
            explanations,
            str(lecture_id),
            payload.customer_identifier,
            user_api_key,
            payload.name,
            payload.email,
        )

        # 6. Atomically save summary, and check for rendezvous
        embeddings_are_complete = False
        async with conn.transaction():
            await db_utils.save_summary(conn, lecture_id, summary_content, metadata)
            embeddings_are_complete = await conn.fetchval(
                "SELECT embeddings_complete FROM lectures WHERE id = $1", lecture_id
            )

        # 7. If the other track is done, perform the final status update
        if embeddings_are_complete:
            await db_utils.set_lecture_status_to_complete(conn, lecture_id)

    except Exception as e:
        logging.error(
            f"[{lecture_id}]: An unexpected error occurred: {e}", exc_info=True
        )
        # The router will catch this and raise an HTTPException to trigger a retry
        raise
    finally:
        if conn and not conn.is_closed():
            await conn.close()
