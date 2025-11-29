import logging
from uuid import UUID
from typing import List

import asyncpg


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


async def query_similar_embeddings(
    conn: asyncpg.Connection,
    lecture_id: UUID,
    query_embedding: List[float],
    limit: int = 5,
) -> List[asyncpg.Record]:
    """
    Query similar embeddings using cosine similarity.
    Uses 1 - (vector <=> $1::vector) for cosine similarity.
    """
    # Convert list to string format for pgvector (same format as used in embedding service)
    query_vector_str = "[" + ",".join(map(str, query_embedding)) + "]"

    return await conn.fetch(
        """
        SELECT 
            e.chunk_id,
            e.slide_id,
            e.lecture_id,
            e.slide_number,
            c.text,
            c.chunk_index,
            1 - (e.vector <=> $1::vector) AS similarity
        FROM embeddings e
        JOIN chunks c ON e.chunk_id = c.id
        WHERE e.lecture_id = $2
        ORDER BY e.vector <=> $1::vector
        LIMIT $3
        """,
        query_vector_str,
        lecture_id,
        limit,
    )


async def get_chunk_context(
    conn: asyncpg.Connection, chunk_ids: List[UUID]
) -> List[asyncpg.Record]:
    """
    Retrieve full text and metadata for matched chunks.
    Also includes OCR and alt text from associated content images.
    """
    if not chunk_ids:
        return []

    chunk_ids_str = [str(cid) for cid in chunk_ids]

    # Get chunks with their slide images' OCR and alt text
    return await conn.fetch(
        """
        SELECT DISTINCT
            c.id,
            c.slide_id,
            c.lecture_id,
            c.slide_number,
            c.chunk_index,
            c.text,
            COALESCE(
                STRING_AGG(DISTINCT si.ocr_text, ' ' ORDER BY si.ocr_text) FILTER (WHERE si.ocr_text IS NOT NULL),
                ''
            ) AS ocr_text,
            COALESCE(
                STRING_AGG(DISTINCT si.alt_text, ' ' ORDER BY si.alt_text) FILTER (WHERE si.alt_text IS NOT NULL),
                ''
            ) AS alt_text
        FROM chunks c
        LEFT JOIN slide_images si ON si.slide_id = c.slide_id AND si.type = 'content'
        WHERE c.id = ANY($1::uuid[])
        GROUP BY c.id, c.slide_id, c.lecture_id, c.slide_number, c.chunk_index, c.text
        ORDER BY c.slide_number, c.chunk_index
        """,
        chunk_ids_str,
    )
