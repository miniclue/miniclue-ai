import logging
import asyncpg
from app.schemas.summary import SummaryPayload
from app.services.summary import db_utils, openai_utils
from app.utils.config import Settings
from app.utils.llm_db_utils import log_llm_call, compute_cost

settings = Settings()


async def process_summary_job(payload: SummaryPayload):
    """
    Orchestrates the entire process of generating a summary for a lecture.
    """
    lecture_id = payload.lecture_id

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
            logging.info(f"Summary for lecture {lecture_id} already exists. Skipping.")
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

        # 5. Call the AI model to synthesize the explanations into a summary
        if settings.mock_llm_calls:
            summary_content, metadata = openai_utils.mock_generate_summary(
                explanations,
                str(lecture_id),
            )
        else:
            summary_content, metadata = await openai_utils.generate_summary(
                explanations,
                str(lecture_id),
                payload.customer_identifier,
                payload.name,
                payload.email,
            )
        # Log LLM call for summary
        try:
            usage = metadata.get("usage") or {}
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)
            cost = compute_cost(
                settings.summary_model, prompt_tokens, completion_tokens
            )
            await log_llm_call(
                conn,
                lecture_id,
                None,
                "summary",
                settings.summary_model,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                cost,
                metadata,
            )
        except Exception:
            logging.error("Failed to log LLM call for summary", exc_info=True)

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
