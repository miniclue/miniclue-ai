import logging
import uuid
from typing import Optional, Tuple

from asyncpg import Connection


async def verify_lecture_exists(conn: Connection, lecture_id: uuid.UUID) -> bool:
    """Verifies that the lecture exists and is not in a terminal state."""
    query = """
        SELECT EXISTS (
            SELECT 1
            FROM lectures
            WHERE id = $1 AND status NOT IN ('failed', 'complete')
        );
    """
    try:
        exists = await conn.fetchval(query, lecture_id)
        if not exists:
            logging.warning(
                f"Lecture {lecture_id} not found or is in a terminal state."
            )
        return exists
    except Exception as e:
        logging.error(f"Error verifying lecture {lecture_id}: {e}", exc_info=True)
        raise


async def get_image_storage_path(
    conn: Connection, slide_image_id: uuid.UUID
) -> Optional[str]:
    """Fetches the storage path for a given slide image."""
    query = "SELECT storage_path FROM slide_images WHERE id = $1;"
    try:
        path = await conn.fetchval(query, slide_image_id)
        if not path:
            logging.warning(
                f"No storage path found for slide_image_id {slide_image_id}"
            )
        return path
    except Exception as e:
        logging.error(
            f"Error fetching storage path for slide_image {slide_image_id}: {e}",
            exc_info=True,
        )
        raise


async def update_image_analysis_results(
    conn: Connection,
    lecture_id: uuid.UUID,
    image_hash: str,
    image_type: str,
    ocr_text: str,
    alt_text: str,
):
    """Propagates analysis results to all instances of an image in a lecture."""
    query = """
        UPDATE slide_images
        SET
            type = $1,
            ocr_text = $2,
            alt_text = $3,
            updated_at = NOW()
        WHERE lecture_id = $4 AND image_hash = $5;
    """
    try:
        await conn.execute(
            query, image_type, ocr_text, alt_text, lecture_id, image_hash
        )
        logging.info(
            f"Updated analysis for image hash {image_hash} in lecture {lecture_id}."
        )
    except Exception as e:
        logging.error(
            f"Error updating analysis for hash {image_hash} in lecture {lecture_id}: {e}",
            exc_info=True,
        )
        raise


async def increment_processed_images_count(
    conn: Connection, lecture_id: uuid.UUID
) -> Tuple[int, int]:
    """Increments the processed images count and returns the new count and the total."""
    query = """
        UPDATE lectures
        SET processed_sub_images = processed_sub_images + 1
        WHERE id = $1
        RETURNING processed_sub_images, total_sub_images;
    """
    try:
        result = await conn.fetchrow(query, lecture_id)
        if not result:
            raise Exception(f"Lecture {lecture_id} not found for incrementing count.")
        processed_count = result["processed_sub_images"]
        total_count = result["total_sub_images"]
        logging.info(
            f"Incremented processed images for lecture {lecture_id}: {processed_count}/{total_count}"
        )
        return processed_count, total_count
    except Exception as e:
        logging.error(
            f"Error incrementing processed image count for lecture {lecture_id}: {e}",
            exc_info=True,
        )
        raise
