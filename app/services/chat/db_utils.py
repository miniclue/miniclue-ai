import logging
import json
from uuid import UUID
from typing import List, Dict, Any

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


async def get_message_history(
    conn: asyncpg.Connection, chat_id: UUID, user_id: UUID, limit: int = 10
) -> List[Dict[str, Any]]:
    """
    Fetch message history from the messages table for a given chat_id.
    Returns list of messages with role and text extracted from parts.
    Messages are returned in chronological order (oldest first).

    Note: This function explicitly checks ownership by joining with chats and lectures
    to bypass RLS policies that require auth.uid() context.

    Args:
        conn: Database connection
        chat_id: Chat ID to fetch messages for
        user_id: User ID to verify ownership
        limit: Maximum number of messages to fetch (default: 10 for 5 turns)

    Returns:
        List of message dicts: [{"role": "user|assistant", "text": "..."}]
    """

    # Fetch messages ordered by created_at DESC (newest first)
    # Explicitly join with chats and lectures to verify ownership and bypass RLS
    try:
        rows = await conn.fetch(
            """
            SELECT m.role, m.parts, m.created_at
            FROM messages m
            INNER JOIN chats c ON c.id = m.chat_id
            INNER JOIN lectures l ON l.id = c.lecture_id
            WHERE m.chat_id = $1
              AND c.user_id = $2
              AND l.user_id = $2
            ORDER BY m.created_at DESC
            LIMIT $3
            """,
            chat_id,
            user_id,
            limit,
        )
    except Exception as e:
        logging.error(
            f"[get_message_history] Database query failed for chat_id={chat_id}, user_id={user_id}: {e}",
            exc_info=True,
        )
        raise

    if not rows:

        return []

    # Extract text from parts JSONB and build message list
    messages = []
    skipped_count = 0
    for idx, row in enumerate(rows):
        parts_raw = row["parts"]

        # Parse JSONB field - asyncpg returns JSONB as string or already parsed dict/list
        if isinstance(parts_raw, str):
            try:
                parts = json.loads(parts_raw)
            except json.JSONDecodeError as e:
                logging.warning(
                    f"[get_message_history] Failed to parse parts JSON for row {idx+1}: {e}, parts={parts_raw}"
                )
                skipped_count += 1
                continue
        else:
            parts = parts_raw

        if not parts:
            skipped_count += 1
            continue

        # Extract text from parts (handle both dict and list formats)
        text_parts = []
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict) and part.get("type") == "text":
                    text = part.get("text", "")
                    if text:
                        text_parts.append(text)
        elif isinstance(parts, dict):
            # Handle single part as dict
            if parts.get("type") == "text":
                text = parts.get("text", "")
                if text:
                    text_parts.append(text)

        # Only include messages with text content
        if text_parts:
            message_text = " ".join(text_parts).strip()
            messages.append(
                {
                    "role": row["role"],
                    "text": message_text,
                }
            )

        else:
            skipped_count += 1

    # Reverse to get chronological order (oldest first)
    messages.reverse()

    return messages
