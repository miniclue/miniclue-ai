import logging
import json

import asyncpg

from app.services.image_analysis import db_utils, llm_utils, s3_utils, pubsub_utils
from app.utils.config import Settings
from app.schemas.image_analysis import ImageAnalysisPayload
from app.utils.llm_utils import get_llm_context
from app.utils.s3_utils import get_s3_client


settings = Settings()


async def process_image_analysis_job(
    payload: ImageAnalysisPayload,
):
    """
    Orchestrates the analysis of a single unique slide image.
    1. Fetches the image from S3.
    2. Sends it to an LLM for analysis (type, ocr_text, alt_text).
    3. Propagates the results to all DB records with the same image hash.
    4. Atomically increments the processed image counter for the lecture.
    5. If it's the last image, triggers the embedding job.
    """
    slide_image_id = payload.slide_image_id
    lecture_id = payload.lecture_id
    image_hash = payload.image_hash
    customer_identifier = payload.customer_identifier
    name = payload.name
    email = payload.email

    # Initialize resources
    conn = None
    s3_client = None
    image_bytes = None

    if not settings.postgres_dsn:
        logging.error("Database settings are not configured.")
        raise RuntimeError("Required settings are not configured.")

    try:
        s3_client = get_s3_client()
        conn = await asyncpg.connect(settings.postgres_dsn, statement_cache_size=0)

        # 1. Verify lecture exists (Defensive Subscriber)
        if not await db_utils.verify_lecture_exists(conn, lecture_id):
            logging.warning(f"Lecture {lecture_id} not found. Stopping job.")
            return

        # 2. Get image path
        storage_path = await db_utils.get_image_storage_path(conn, slide_image_id)
        if not storage_path:
            logging.error(f"Storage path for slide_image {slide_image_id} not found.")
            return  # Acknowledge the message and stop.

        # 3. Download image from S3
        image_bytes = s3_utils.download_image(
            s3_client, settings.s3_bucket_name, storage_path
        )

        # 4. Fetch user API context (required)
        user_client, _ = await get_llm_context(
            customer_identifier, settings.image_analysis_model
        )

        # 5. Analyze image with selected model
        # Perform image analysis and capture metadata
        analysis_result, metadata = await llm_utils.analyze_image(
            image_bytes=image_bytes,
            lecture_id=str(lecture_id),
            slide_image_id=str(slide_image_id),
            customer_identifier=customer_identifier,
            name=name,
            email=email,
            client=user_client,
        )

        # Use a transaction for the final updates to ensure atomicity
        async with conn.transaction():
            # 5. Propagate results to all matching images, including metadata
            await db_utils.update_image_analysis_results(
                conn=conn,
                lecture_id=lecture_id,
                image_hash=image_hash,
                image_type=analysis_result.image_type,
                ocr_text=analysis_result.ocr_text,
                alt_text=analysis_result.alt_text,
                metadata=metadata,
            )

            # 6. Increment counter and check if last job
            (
                processed_count,
                total_count,
            ) = await db_utils.increment_processed_images_count(conn, lecture_id)

        # 7. Trigger embedding job if all images are processed
        if total_count > 0 and processed_count == total_count:
            pubsub_utils.publish_embedding_job(
                lecture_id,
                customer_identifier=customer_identifier,
                name=name,
                email=email,
            )

    except Exception as e:
        logging.error(
            f"Image analysis job failed for slide_image_id {slide_image_id}: {e}",
            exc_info=True,
        )
        if conn:
            error_info = {
                "service": "image_analysis",
                "slide_image_id": str(slide_image_id),
                "error": str(e),
            }
            await conn.execute(
                "UPDATE lectures SET embedding_error_details = $1::jsonb, updated_at = NOW() WHERE id = $2",
                json.dumps(error_info),
                lecture_id,
            )
        # Re-raising ensures the message is not acknowledged and will be redelivered
        raise
    finally:
        # Clean up resources explicitly to prevent memory leaks
        if image_bytes is not None:
            del image_bytes
        if s3_client:
            s3_client.close()
        if conn:
            await conn.close()
