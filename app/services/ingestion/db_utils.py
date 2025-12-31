from uuid import UUID

import asyncpg
from app.utils.sanitize import sanitize_text


async def update_lecture_status(
    conn: asyncpg.Connection,
    lecture_id: UUID,
    status: str,
    embedding_error_details: str | None = None,
):
    """Updates the status and optionally the embedding_error_details of a lecture."""
    if embedding_error_details:
        await conn.execute(
            "UPDATE lectures SET status=$1, embedding_error_details=$2::jsonb, updated_at=NOW() WHERE id=$3",
            status,
            embedding_error_details,
            lecture_id,
        )
    else:
        await conn.execute(
            "UPDATE lectures SET status=$1, updated_at=NOW() WHERE id=$2",
            status,
            lecture_id,
        )


async def set_lecture_parsing(
    conn: asyncpg.Connection, lecture_id: UUID, total_slides: int
):
    """Sets the lecture status to 'parsing' and records the total slide count."""
    await conn.execute(
        "UPDATE lectures SET status='parsing', total_slides=$1, updated_at=NOW() WHERE id=$2",
        total_slides,
        lecture_id,
    )


async def update_lecture_sub_image_count(
    conn: asyncpg.Connection, lecture_id: UUID, total_sub_images: int
):
    """Saves the final count of unique sub-images for the lecture."""
    await conn.execute(
        "UPDATE lectures SET total_sub_images=$1, updated_at=NOW() WHERE id=$2",
        total_sub_images,
        lecture_id,
    )


async def get_or_create_slide(
    conn: asyncpg.Connection, lecture_id: UUID, slide_number: int, raw_text: str
) -> UUID:
    """Gets a slide if it exists, otherwise creates it and returns the new ID."""
    slide_id = await conn.fetchval(
        "SELECT id FROM slides WHERE lecture_id=$1 AND slide_number=$2",
        lecture_id,
        slide_number,
    )
    if slide_id:
        return slide_id

    safe_raw_text = sanitize_text(raw_text)
    return await conn.fetchval(
        """
        INSERT INTO slides (lecture_id, slide_number, raw_text)
        VALUES ($1, $2, $3)
        ON CONFLICT (lecture_id, slide_number) DO UPDATE
        SET raw_text = EXCLUDED.raw_text
        RETURNING id
        """,
        lecture_id,
        slide_number,
        safe_raw_text,
    )


async def get_or_create_chunk(
    conn: asyncpg.Connection,
    slide_id: UUID,
    lecture_id: UUID,
    slide_number: int,
    chunk_index: int,
    text_chunk: str,
    token_count: int,
) -> UUID:
    """Gets a chunk or creates it, returning its ID."""
    chunk_id = await conn.fetchval(
        "SELECT id FROM chunks WHERE slide_id=$1 AND chunk_index=$2",
        slide_id,
        chunk_index,
    )
    if chunk_id:
        return chunk_id

    safe_text = sanitize_text(text_chunk) or ""
    return await conn.fetchval(
        """
        INSERT INTO chunks
          (slide_id, lecture_id, slide_number, chunk_index, text, token_count)
        VALUES ($1, $2, $3, $4, $5, $6)
        ON CONFLICT (slide_id, chunk_index) DO NOTHING
        RETURNING id
        """,
        slide_id,
        lecture_id,
        slide_number,
        chunk_index,
        safe_text,
        token_count,
    )


async def insert_slide_image(
    conn: asyncpg.Connection,
    slide_id: UUID,
    lecture_id: UUID,
    image_hash: str,
    storage_path: str,
    image_type: str | None,
) -> UUID:
    """Inserts a record for a slide image and returns its ID."""
    return await conn.fetchval(
        """
        INSERT INTO slide_images
          (slide_id, lecture_id, image_hash, storage_path, type)
        VALUES ($1, $2, $3, $4, $5)
        RETURNING id
        """,
        slide_id,
        lecture_id,
        image_hash,
        storage_path,
        image_type,
    )


async def get_slides_with_images_for_lecture(
    conn: asyncpg.Connection, lecture_id: UUID
):
    """
    Fetches all slides for a given lecture with their corresponding full-slide image path,
    ready for dispatching explanation jobs.
    """
    return await conn.fetch(
        """
        SELECT
            s.id,
            s.slide_number,
            si.storage_path AS slide_image_path
        FROM
            slides s
        LEFT JOIN
            slide_images si ON s.id = si.slide_id AND si.type = 'full_slide_render'
        WHERE
            s.lecture_id = $1
        ORDER BY
            s.slide_number
        """,
        lecture_id,
    )
