import logging
from uuid import UUID
import json

import asyncpg
from app.schemas.explanation import ExplanationResult
from app.utils.sanitize import sanitize_text, sanitize_json


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


async def save_explanation(
    conn: asyncpg.Connection,
    slide_id: UUID,
    lecture_id: UUID,
    slide_number: int,
    result: ExplanationResult,
    metadata: dict,
) -> None:
    """Saves the AI's explanation and metadata to the 'explanations' table."""
    safe_content = sanitize_text(result.explanation) or ""
    safe_metadata = sanitize_json(metadata) if metadata is not None else {}
    await conn.execute(
        """
        INSERT INTO explanations (slide_id, lecture_id, slide_number, content, slide_type, metadata)
        VALUES ($1, $2, $3, $4, $5, $6::jsonb)
        ON CONFLICT (slide_id) DO NOTHING
        """,
        slide_id,
        lecture_id,
        slide_number,
        safe_content,
        result.slide_purpose,
        json.dumps(safe_metadata),
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
            # Don't change status here - that's the summary service's responsibility
            return True
        return False
