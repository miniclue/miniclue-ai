import logging
from uuid import UUID
import asyncpg
from app.utils.config import Settings
import json

settings = Settings()


async def verify_lecture_exists(conn: asyncpg.Connection, lecture_id: UUID) -> bool:
    """
    Verifies that the lecture exists and is not in a terminal state.
    """
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


async def check_summary_exists(conn: asyncpg.Connection, lecture_id: UUID) -> bool:
    """
    Checks if a summary for the given lecture ID already exists.
    """
    return await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM summaries WHERE lecture_id = $1)",
        lecture_id,
    )


async def get_all_explanations(conn: asyncpg.Connection, lecture_id: UUID) -> list[str]:
    """
    Retrieves all explanation content for a given lecture, ordered by slide number.
    """
    records = await conn.fetch(
        """
        SELECT content FROM explanations
        WHERE lecture_id = $1
        ORDER BY slide_number ASC
        """,
        lecture_id,
    )
    return [record["content"] for record in records]


async def save_summary(
    conn: asyncpg.Connection, lecture_id: UUID, content: str, metadata: dict
):
    """
    Saves the generated summary and its metadata to the database.
    Performs an "upsert" to handle potential retries.
    """
    await conn.execute(
        """
        INSERT INTO summaries (lecture_id, content, metadata)
        VALUES ($1, $2, $3::jsonb)
        ON CONFLICT (lecture_id) DO UPDATE
        SET content = EXCLUDED.content,
            metadata = EXCLUDED.metadata,
            updated_at = NOW()
        """,
        lecture_id,
        content,
        json.dumps(metadata),
    )


async def finalize_explanation_track(lecture_id: UUID) -> bool:
    """
    Atomically updates the lecture status to 'summarising' (if it's 'explaining')
    and returns the value of the 'embeddings_complete' flag.

    This is a key part of the rendezvous logic to determine if this is the
    last processing track to complete.
    """
    conn = await asyncpg.connect(settings.postgres_dsn)
    try:
        async with conn.transaction():
            # This transaction ensures that we both update the status (if needed)
            # and get the rendezvous flag in a single atomic operation.
            await conn.execute(
                """
                UPDATE lectures
                SET status = 'summarising'
                WHERE id = $1 AND status = 'explaining'
                """,
                lecture_id,
            )
            # Now, regardless of whether the above update did anything,
            # we fetch the current flag.
            embeddings_complete = await conn.fetchval(
                "SELECT embeddings_complete FROM lectures WHERE id = $1",
                lecture_id,
            )
            return embeddings_complete if embeddings_complete is not None else False
    finally:
        await conn.close()


async def set_lecture_status_to_complete(conn: asyncpg.Connection, lecture_id: UUID):
    """
    Sets the lecture's status to 'complete' and records the completion time.
    """
    logging.info(f"[{lecture_id}]: Setting lecture status to 'complete'.")
    await conn.execute(
        """
        UPDATE lectures
        SET status = 'complete', completed_at = NOW()
        WHERE id = $1
        """,
        lecture_id,
    )
