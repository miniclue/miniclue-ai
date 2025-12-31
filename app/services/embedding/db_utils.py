from uuid import UUID
from typing import List, Dict, Any, Optional

import asyncpg


async def get_lecture_chunks(
    conn: asyncpg.Connection, lecture_id: UUID
) -> List[asyncpg.Record]:
    """Fetch all chunk records for a given lecture."""
    return await conn.fetch(
        """
        SELECT id, slide_id, lecture_id, slide_number, text
        FROM chunks
        WHERE lecture_id = $1
        ORDER BY slide_number, chunk_index
        """,
        lecture_id,
    )


async def get_content_images_for_lecture(
    conn: asyncpg.Connection, lecture_id: UUID
) -> List[asyncpg.Record]:
    """Fetch all 'content' images and their text for a given lecture."""
    return await conn.fetch(
        """
        SELECT slide_id, ocr_text, alt_text
        FROM slide_images
        WHERE lecture_id = $1 AND type = 'content'
        """,
        lecture_id,
    )


async def batch_upsert_embeddings(
    conn: asyncpg.Connection, embeddings: List[Dict[str, Any]]
):
    """
    Batch inserts or updates embeddings in the database.
    'embeddings' is a list of dicts with keys matching the table columns.
    """
    if not embeddings:
        return

    # Convert UUIDs to strings and vector/metadata to JSON strings for the query
    insert_data = [
        (
            str(e["chunk_id"]),
            str(e["slide_id"]),
            str(e["lecture_id"]),
            e["slide_number"],
            e["vector"],
            e["metadata"],
        )
        for e in embeddings
    ]

    await conn.executemany(
        """
        INSERT INTO embeddings (chunk_id, slide_id, lecture_id, slide_number, vector, metadata)
        VALUES ($1, $2, $3, $4, $5::vector, $6::jsonb)
        ON CONFLICT (chunk_id) DO UPDATE
          SET vector = EXCLUDED.vector,
              metadata = EXCLUDED.metadata,
              updated_at = NOW()
        """,
        insert_data,
    )


async def set_embeddings_complete(
    conn: asyncpg.Connection, lecture_id: UUID
) -> Optional[str]:
    """
    Set the 'embeddings_complete' flag to TRUE for a lecture and return its status.
    """
    return await conn.fetchval(
        """
        UPDATE lectures
           SET embeddings_complete = TRUE, updated_at = NOW()
         WHERE id = $1
        RETURNING status
        """,
        lecture_id,
    )


async def set_lecture_status_to_complete(conn: asyncpg.Connection, lecture_id: UUID):
    """Set the lecture's status to 'complete'."""
    await conn.execute(
        """
        UPDATE lectures
           SET status = 'complete', completed_at = NOW(), updated_at = NOW()
         WHERE id = $1
        """,
        lecture_id,
    )
