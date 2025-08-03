import logging
import json

import asyncpg
import boto3

from app.services.image_analysis import db_utils, llm_utils, s3_utils, pubsub_utils
from app.utils.config import Settings
from app.schemas.image_analysis import ImageAnalysisPayload
from app.utils.llm_db_utils import log_llm_call, compute_cost

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
    conn = None

    try:
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

        # 4. Analyze image with OpenAI
        # Perform image analysis and capture metadata
        if settings.mock_llm_calls:
            analysis_result, metadata = llm_utils.mock_analyze_image(
                image_bytes,
                str(lecture_id),
                str(slide_image_id),
            )
        else:
            analysis_result, metadata = await llm_utils.analyze_image(
                image_bytes=image_bytes,
                lecture_id=str(lecture_id),
                slide_image_id=str(slide_image_id),
                customer_identifier=customer_identifier,
                name=name,
                email=email,
            )
        # Log LLM call for image analysis
        try:
            # Extract token usage from metadata
            usage = metadata.get("usage", {}) if metadata else {}
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)
            cost = compute_cost(
                settings.image_analysis_model, prompt_tokens, completion_tokens
            )
            # Pass computed cost, no metadata for now, and None for slide_id
            await log_llm_call(
                conn,
                lecture_id,
                None,
                "image_analysis",
                settings.image_analysis_model,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                cost,
            )
        except Exception:
            logging.error("Failed to log LLM call for image analysis", exc_info=True)

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
            processed_count, total_count = (
                await db_utils.increment_processed_images_count(conn, lecture_id)
            )

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
                "UPDATE lectures SET search_error_details = $1::jsonb, updated_at = NOW() WHERE id = $2",
                json.dumps(error_info),
                lecture_id,
            )
        # Re-raising ensures the message is not acknowledged and will be redelivered
        raise
    finally:
        if conn:
            await conn.close()
