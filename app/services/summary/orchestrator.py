import logging
import asyncpg
from app.schemas.summary import SummaryPayload
from app.services.summary import db_utils, openai_utils
from app.utils.config import Settings

settings = Settings()


async def process_summary_job(payload: SummaryPayload):
    """
    Orchestrates the entire summary generation process for a given lecture.
    This is the main entry point for the summary service.
    """
    lecture_id = payload.lecture_id
    logging.info(f"[{lecture_id}]: Starting summary process.")

    try:
        conn = await asyncpg.connect(settings.postgres_dsn, statement_cache_size=0)

        # 1. Verify the lecture exists and is in a processable state
        if not await db_utils.verify_lecture_exists(conn, lecture_id):
            return

        # 2. Check if a summary already exists to prevent reprocessing
        if await db_utils.check_summary_exists(conn, lecture_id):
            logging.info(f"[{lecture_id}]: Summary already exists. Skipping.")
            return

        # 3. Gather all slide explanations
        explanations = await db_utils.get_all_explanations(conn, lecture_id)
        if not explanations:
            logging.warning(
                f"[{lecture_id}]: No explanations found. Cannot generate summary."
            )
            # Optionally, you could set the lecture to a failed state here.
            return

        # 4. Call the AI model to synthesize the explanations into a summary
        logging.info(
            f"[{lecture_id}]: Generating summary from {len(explanations)} explanations."
        )

        if settings.mock_llm_calls:
            summary_content, metadata = openai_utils.mock_generate_summary(
                explanations,
                str(lecture_id),
            )
        else:
            summary_content, metadata = await openai_utils.generate_summary(
                explanations,
                str(lecture_id),
            )

        # 5. Atomically save summary, update status, and check for rendezvous
        embeddings_are_complete = False
        async with conn.transaction():
            logging.info(
                f"[{lecture_id}]: Saving summary and finalizing explanation track in transaction."
            )
            await db_utils.save_summary(conn, lecture_id, summary_content, metadata)

            # Idempotently set status to 'summarising' and get the rendezvous flag
            await conn.execute(
                "UPDATE lectures SET status = 'summarising' WHERE id = $1", lecture_id
            )
            embeddings_are_complete = await conn.fetchval(
                "SELECT embeddings_complete FROM lectures WHERE id = $1", lecture_id
            )

        # 6. If the other track is done, perform the final status update
        if embeddings_are_complete:
            logging.info(
                f"[{lecture_id}]: Rendezvous! Embeddings are complete. Marking lecture as 'complete'."
            )
            await db_utils.set_lecture_status_to_complete(conn, lecture_id)
        else:
            logging.info(
                f"[{lecture_id}]: Summary complete. Waiting for embedding track to finish."
            )

        logging.info(f"[{lecture_id}]: Summary process finished successfully.")

    except Exception as e:
        logging.error(
            f"[{lecture_id}]: An unexpected error occurred: {e}", exc_info=True
        )
        # The router will catch this and raise an HTTPException to trigger a retry
        raise
    finally:
        if conn and not conn.is_closed():
            await conn.close()
