import logging
import uuid

import asyncpg
import boto3
from openai import AsyncOpenAI

from app.services.image_analysis.db_utils import (
    get_image_storage_path,
    increment_processed_images_count,
    update_image_analysis_results,
    verify_lecture_exists,
)
from app.services.image_analysis.openai_utils import (
    ImageAnalysisResult,
    analyze_image_with_openai,
)
from app.services.image_analysis.pubsub_utils import publish_embedding_job
from app.services.image_analysis.s3_utils import download_image
from app.utils.config import Settings

settings = Settings()


async def process_image_analysis_job(
    slide_image_id: uuid.UUID, lecture_id: uuid.UUID, image_hash: str
):
    """
    Orchestrates the analysis of a single unique slide image.
    1. Fetches the image from S3.
    2. Sends it to an LLM for analysis (type, ocr_text, alt_text).
    3. Propagates the results to all DB records with the same image hash.
    4. Atomically increments the processed image counter for the lecture.
    5. If it's the last image, triggers the embedding job.
    """
    logging.info(
        f"Starting image analysis for slide_image_id={slide_image_id}, "
        f"lecture_id={lecture_id}, image_hash={image_hash}"
    )

    # Initialize clients
    if not settings.postgres_dsn:
        logging.error("Database settings are not configured.")
        raise RuntimeError("Required settings are not configured.")

    s3_client = boto3.client(
        "s3",
        aws_access_key_id=settings.s3_access_key or None,
        aws_secret_access_key=settings.s3_secret_key or None,
        endpoint_url=settings.s3_endpoint_url or None,
    )
    openai_client = AsyncOpenAI(
        api_key=settings.gemini_api_key, base_url=settings.gemini_api_base_url
    )
    conn = None

    try:
        conn = await asyncpg.connect(settings.postgres_dsn)
        logging.info("Established connections to DB.")

        # 1. Verify lecture exists (Defensive Subscriber)
        if not await verify_lecture_exists(conn, lecture_id):
            logging.warning(f"Lecture {lecture_id} not found. Stopping job.")
            return

        # 2. Get image path
        storage_path = await get_image_storage_path(conn, slide_image_id)
        if not storage_path:
            logging.error(f"Storage path for slide_image {slide_image_id} not found.")
            return  # Acknowledge the message and stop.

        # 3. Download image from S3
        image_bytes = download_image(s3_client, settings.s3_bucket_name, storage_path)

        if settings.mock_llm_calls:
            logging.info("Mocking LLM call for image analysis.")
            analysis_result = ImageAnalysisResult(
                type="mock", ocr_text="mock", alt_text="mock"
            )
        else:
            # 4. Analyze image with OpenAI
            analysis_result = await analyze_image_with_openai(
                openai_client, image_bytes
            )

        # Use a transaction for the final updates to ensure atomicity
        async with conn.transaction():
            # 5. Propagate results to all matching images
            await update_image_analysis_results(
                conn=conn,
                lecture_id=lecture_id,
                image_hash=image_hash,
                image_type=analysis_result.image_type,
                ocr_text=analysis_result.ocr_text,
                alt_text=analysis_result.alt_text,
            )

            # 6. Increment counter and check if last job
            processed_count, total_count = await increment_processed_images_count(
                conn, lecture_id
            )

        # 7. Trigger embedding job if all images are processed
        if total_count > 0 and processed_count == total_count:
            logging.info(
                f"All {total_count} images for lecture {lecture_id} have been processed. "
                "Triggering embedding job."
            )
            publish_embedding_job(lecture_id)
        else:
            logging.info(
                f"Processed {processed_count}/{total_count} images for lecture {lecture_id}."
            )

    except Exception as e:
        logging.error(
            f"Image analysis job failed for slide_image_id {slide_image_id}: {e}",
            exc_info=True,
        )
        # Re-raising ensures the message is not acknowledged and will be redelivered
        raise
    finally:
        if conn:
            await conn.close()
            logging.info("Postgres connection closed.")

    logging.info(
        f"Successfully finished image analysis for slide_image_id={slide_image_id}"
    )
