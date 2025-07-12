import asyncpg
from uuid import UUID
from typing import List


async def fetch_explanations(conn: asyncpg.Connection, lecture_id: UUID) -> List[str]:
    """Fetch slide explanations for a given lecture."""
    rows = await conn.fetch(
        """
        SELECT content
          FROM explanations
         WHERE lecture_id = $1
         ORDER BY slide_number
        """,
        lecture_id,
    )
    return [r["content"] for r in rows]


async def persist_summary_and_update_lecture(
    conn: asyncpg.Connection, lecture_id: UUID, summary: str, metadata_str: str
):
    """Persist summary and update lecture status to 'complete'."""
    async with conn.transaction():
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
            summary,
            metadata_str,
        )
        await conn.execute(
            """
            UPDATE lectures
               SET status = 'complete',
                   completed_at = NOW()
             WHERE id = $1
            """,
            lecture_id,
        )
