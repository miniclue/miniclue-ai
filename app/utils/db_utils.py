import logging
from uuid import UUID

import asyncpg


async def verify_lecture_exists(
    conn: asyncpg.Connection, lecture_id: UUID, allow_complete: bool = False
) -> bool:
    """Verifies that the lecture exists and is not in a terminal state."""
    excluded_statuses = ["failed"]
    if not allow_complete:
        excluded_statuses.append("complete")

    exists = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM lectures
            WHERE id = $1 AND status::text NOT IN (SELECT unnest($2::text[]))
        );
        """,
        lecture_id,
        excluded_statuses,
    )
    if not exists:
        logging.warning(f"Lecture {lecture_id} not found or is in a terminal state.")
    return exists


async def verify_lecture_exists_and_ownership(
    conn: asyncpg.Connection, lecture_id: UUID, user_id: UUID
) -> bool:
    """Verifies that the lecture exists and the user owns it."""
    exists = await conn.fetchval(
        """
        SELECT EXISTS (
            SELECT 1
            FROM lectures
            WHERE id = $1 AND user_id = $2
        );
        """,
        lecture_id,
        user_id,
    )
    if not exists:
        logging.warning(
            f"Lecture {lecture_id} not found or user {user_id} does not own it."
        )
    return exists
