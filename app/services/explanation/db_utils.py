import logging
from uuid import UUID
from typing import Optional, Tuple
import json

import asyncpg
from app.schemas.explanation import ExplanationResult


async def verify_lecture_exists(conn: asyncpg.Connection, lecture_id: UUID) -> bool:
    """Verifies that the lecture exists and is not in a terminal state."""
    exists = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM lectures
            WHERE id = $1 AND status NOT IN ('failed', 'complete')
        );
        """,
        lecture_id,
    )
    if not exists:
        logging.warning(f"Lecture {lecture_id} not found or is in a terminal state.")
    return exists


async def explanation_exists(conn: asyncpg.Connection, slide_id: UUID) -> bool:
    """Checks if an explanation for the given slide_id already exists."""
    return await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM explanations WHERE slide_id = $1)",
        slide_id,
    )


async def get_slide_context(
    conn: asyncpg.Connection, lecture_id: UUID, current_slide_number: int
) -> Tuple[Optional[str], Optional[str]]:
    """
    Fetches the raw text of the previous and next slides for context.
    """
    # Fetch previous slide's text
    prev_text = await conn.fetchval(
        """
        SELECT raw_text FROM slides
        WHERE lecture_id = $1 AND slide_number = $2
        """,
        lecture_id,
        current_slide_number - 1,
    )

    # Fetch next slide's text
    next_text = await conn.fetchval(
        """
        SELECT raw_text FROM slides
        WHERE lecture_id = $1 AND slide_number = $2
        """,
        lecture_id,
        current_slide_number + 1,
    )

    return prev_text, next_text


async def save_explanation(
    conn: asyncpg.Connection,
    slide_id: UUID,
    lecture_id: UUID,
    slide_number: int,
    result: ExplanationResult,
    metadata: dict,
) -> None:
    """Saves the AI's explanation and metadata to the 'explanations' table."""
    await conn.execute(
        """
        INSERT INTO explanations (slide_id, lecture_id, slide_number, content, one_liner, slide_type, metadata)
        VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
        ON CONFLICT (slide_id) DO NOTHING
        """,
        slide_id,
        lecture_id,
        slide_number,
        result.explanation,
        result.one_liner,
        result.slide_purpose,
        json.dumps(metadata),
    )


async def increment_progress_and_check_completion(
    conn: asyncpg.Connection, lecture_id: UUID
) -> bool:
    """
    Atomically increments the processed_slides count and returns if the lecture is complete.
    """
    async with conn.transaction():
        # Increment the counter
        await conn.execute(
            "UPDATE lectures SET processed_slides = processed_slides + 1 WHERE id = $1",
            lecture_id,
        )
        # Check if all slides are processed
        progress = await conn.fetchrow(
            "SELECT processed_slides, total_slides FROM lectures WHERE id = $1",
            lecture_id,
        )
        if progress and progress["processed_slides"] >= progress["total_slides"]:
            await conn.execute(
                "UPDATE lectures SET status = 'summarising' WHERE id = $1",
                lecture_id,
            )
            return True
    return False
