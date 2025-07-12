import logging
from uuid import UUID

import asyncpg

from app.services.embedding.db_utils import (
    enqueue_explanation_job_if_complete,
    get_chunk_text,
    update_slide_progress,
    upsert_embedding,
)
from app.services.embedding.openai_utils import get_embedding, mock_get_embedding
from app.utils.config import Settings

settings = Settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:     %(message)s",
)


async def embed(chunk_id: UUID, slide_id: UUID, lecture_id: UUID, slide_number: int):
    """
    Consume embedding job: generate vector for a text chunk and persist,
    update slide progress, and enqueue explanation when complete.
    """
    logging.info(
        f"Starting embedding for chunk_id={chunk_id}, slide_number={slide_number}"
    )
    if not settings.postgres_dsn:
        logging.error("Postgres DSN not configured")
        raise RuntimeError("Postgres DSN not configured")

    conn = await asyncpg.connect(settings.postgres_dsn)
    try:
        text = await get_chunk_text(conn, chunk_id)
        if settings.mock_llm_calls:
            vector, metadata = mock_get_embedding(text)
        else:
            vector, metadata = get_embedding(text)

        async with conn.transaction():
            await upsert_embedding(
                conn,
                chunk_id,
                slide_id,
                lecture_id,
                slide_number,
                vector,
                metadata,
            )

            processed, total = await update_slide_progress(conn, slide_id)

            await enqueue_explanation_job_if_complete(
                conn, slide_id, lecture_id, slide_number, processed, total
            )

        logging.info(f"Finished embedding for chunk_id={chunk_id}")
    finally:
        await conn.close()
        logging.info("Postgres connection closed for embedding")
