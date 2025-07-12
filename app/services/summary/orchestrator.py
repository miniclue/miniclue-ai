import logging
from uuid import UUID

import asyncpg

from app.services.summary.db_utils import (
    fetch_explanations,
    persist_summary_and_update_lecture,
)
from app.services.summary.openai_utils import (
    generate_summary,
    mock_generate_summary,
)
from app.utils.config import Settings

settings = Settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:     %(message)s",
)


async def summarize(lecture_id: UUID):
    """
    Consume summarization job: generate summary for a lecture, persist result,
    update lecture status to complete.
    """
    logging.info(f"Starting summarization for lecture_id={lecture_id}")
    if not settings.postgres_dsn:
        logging.error("Postgres DSN not configured")
        raise RuntimeError("Postgres DSN not configured")
    if not settings.openai_api_key:
        logging.error("OpenAI API key not configured")
        raise RuntimeError("OpenAI API key not configured")

    conn = await asyncpg.connect(settings.postgres_dsn)
    try:
        slide_explanations = await fetch_explanations(conn, lecture_id)

        if settings.mock_llm_calls:
            summary, metadata_str = mock_generate_summary(slide_explanations)
        else:
            summary, metadata_str = generate_summary(slide_explanations)

        await persist_summary_and_update_lecture(
            conn, lecture_id, summary, metadata_str
        )

        logging.info(f"Finished summarization for lecture_id={lecture_id}")
    except Exception as e:
        logging.error(f"Summarization failed for lecture_id={lecture_id}: {e}")
        raise
    finally:
        if conn and not conn.is_closed():
            await conn.close()
            logging.info("Postgres connection closed for summarization")
