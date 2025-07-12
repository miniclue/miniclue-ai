import json
from uuid import UUID
from typing import List, Tuple

import asyncpg

from app.utils.config import Settings

settings = Settings()


async def fetch_context(
    conn: asyncpg.Connection, lecture_id: UUID, slide_number: int
) -> Tuple[List[str], str]:
    """Fetch previous one-liners for context."""
    rows = await conn.fetch(
        """
        SELECT slide_number, one_liner
          FROM explanations
         WHERE lecture_id = $1
           AND slide_number < $2
         ORDER BY slide_number DESC
         LIMIT 3
        """,
        lecture_id,
        slide_number,
    )
    context_recap = [r["one_liner"] for r in reversed(rows)]
    previous_one_liner = rows[0]["one_liner"] if rows else ""
    return context_recap, previous_one_liner


async def fetch_slide_text(conn: asyncpg.Connection, slide_id: UUID) -> str:
    """Fetch full slide text."""
    row = await conn.fetchrow(
        """
        SELECT raw_text
          FROM slides
         WHERE id = $1
        """,
        slide_id,
    )
    return row.get("raw_text") or "" if row else ""


async def fetch_image_data(
    conn: asyncpg.Connection, lecture_id: UUID, slide_number: int
) -> Tuple[List[str], List[str]]:
    """Fetch non-decorative image OCR and alt texts."""
    img_rows = await conn.fetch(
        """
        SELECT ocr_text, alt_text
          FROM slide_images
         WHERE lecture_id = $1
           AND slide_number = $2
           AND type <> 'decorative'
         ORDER BY image_index
        """,
        lecture_id,
        slide_number,
    )
    ocr_texts = [r["ocr_text"] or "" for r in img_rows]
    alt_texts = [r["alt_text"] or "" for r in img_rows]
    return ocr_texts, alt_texts


async def fetch_related_concepts(
    conn: asyncpg.Connection,
    lecture_id: UUID,
    exclude_slide_number: int,
    query_vector_str: str,
    limit: int = 5,
) -> list[str]:
    """Fetch top-K related concept texts via vector similarity (RAG), excluding the current slide."""
    # Join embeddings with chunks to retrieve the actual text, excluding the current slide
    rows = await conn.fetch(
        """
        SELECT c.text
          FROM embeddings e
          JOIN chunks c ON c.id = e.chunk_id
         WHERE e.lecture_id = $1
           AND e.slide_number <> $2
         ORDER BY e.vector <#> $3::vector
         LIMIT $4
        """,
        lecture_id,
        exclude_slide_number,
        query_vector_str,
        limit,
    )
    return [r["text"] for r in rows]


async def persist_explanation_and_update_progress(
    conn: asyncpg.Connection,
    slide_id: UUID,
    lecture_id: UUID,
    slide_number: int,
    one_liner: str,
    content: str,
    slide_type: str,
    metadata_str: str,
):
    """Persist the explanation and update lecture progress in a transaction."""
    async with conn.transaction():
        await conn.execute(
            """
            INSERT INTO explanations
              (slide_id, lecture_id, slide_number, content, one_liner, slide_type, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            ON CONFLICT (slide_id) DO UPDATE
              SET content = EXCLUDED.content,
                  one_liner = EXCLUDED.one_liner,
                  slide_type = EXCLUDED.slide_type,
                  metadata = EXCLUDED.metadata,
                  updated_at = NOW()
            """,
            slide_id,
            lecture_id,
            slide_number,
            content,
            one_liner,
            slide_type,
            metadata_str,
        )

        await conn.execute(
            "UPDATE lectures SET processed_slides = processed_slides + 1 WHERE id = $1",
            lecture_id,
        )

        progress = await conn.fetchrow(
            "SELECT processed_slides, total_slides FROM lectures WHERE id = $1",
            lecture_id,
        )
        # Enqueue summary if complete
        if progress and progress["processed_slides"] == progress["total_slides"]:
            await conn.execute(
                "UPDATE lectures SET status = 'summarising' WHERE id = $1",
                lecture_id,
            )
            payload = {"lecture_id": str(lecture_id)}
            await conn.execute(
                "SELECT pgmq.send($1::text, $2::jsonb)",
                settings.summary_queue,
                json.dumps(payload),
            )
