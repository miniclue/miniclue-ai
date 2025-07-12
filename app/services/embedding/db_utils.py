import json
import logging
from uuid import UUID

import asyncpg

from app.utils.config import Settings

settings = Settings()

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


async def get_chunk_text(conn: asyncpg.Connection, chunk_id: UUID) -> str:
    """Fetch text chunk from the database."""
    row = await conn.fetchrow("SELECT text FROM chunks WHERE id=$1", str(chunk_id))
    if not row:
        logging.error(f"No chunk found with id={chunk_id}")
        raise ValueError(f"No chunk found with id={chunk_id}")
    return row["text"]


async def upsert_embedding(
    conn: asyncpg.Connection,
    chunk_id: UUID,
    slide_id: UUID,
    lecture_id: UUID,
    slide_number: int,
    vector: str,
    metadata: str,
):
    """Upsert embedding into the database."""
    await conn.execute(
        """
        INSERT INTO embeddings (chunk_id, slide_id, lecture_id, slide_number, vector, metadata)
        VALUES ($1, $2, $3, $4, $5::vector, $6::jsonb)
        ON CONFLICT (chunk_id) DO UPDATE
          SET vector = EXCLUDED.vector,
              updated_at = NOW()
        """,
        str(chunk_id),
        str(slide_id),
        str(lecture_id),
        slide_number,
        vector,
        metadata,
    )


async def update_slide_progress(
    conn: asyncpg.Connection, slide_id: UUID
) -> tuple[int, int]:
    """Update slide progress and return processed and total chunk counts."""
    updated = await conn.fetchrow(
        """
        WITH updated AS (
          UPDATE slides
             SET processed_chunks = processed_chunks + 1
           WHERE id = $1
           RETURNING processed_chunks, total_chunks
        )
        SELECT processed_chunks, total_chunks FROM updated
        """,
        str(slide_id),
    )
    processed = updated["processed_chunks"]
    total = updated["total_chunks"]
    logging.info(
        f"Slide {slide_id}: processed_chunks={processed}, total_chunks={total}"
    )
    return processed, total


async def _check_and_update_lecture_status(conn: asyncpg.Connection, lecture_id: UUID):
    """Update lecture status to 'explaining' if all slides done."""
    incomplete = await conn.fetchval(
        """
        SELECT COUNT(*) FROM slides
         WHERE lecture_id = $1
           AND processed_chunks < total_chunks
        """,
        str(lecture_id),
    )
    if incomplete == 0:
        await conn.execute(
            "UPDATE lectures SET status='explaining' WHERE id = $1",
            str(lecture_id),
        )
        logging.info(f"Lecture {lecture_id} status updated to explaining")


async def enqueue_explanation_job_if_complete(
    conn: asyncpg.Connection,
    slide_id: UUID,
    lecture_id: UUID,
    slide_number: int,
    processed: int,
    total: int,
):
    """Enqueue explanation job if slide is complete."""
    if processed == total:
        payload = {
            "slide_id": str(slide_id),
            "lecture_id": str(lecture_id),
            "slide_number": slide_number,
        }
        await conn.execute(
            "SELECT pgmq.send($1::text, $2::jsonb)",
            settings.explanation_queue,
            json.dumps(payload),
        )
        await _check_and_update_lecture_status(conn, lecture_id)
