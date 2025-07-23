import logging
import asyncpg
import boto3
import json

from app.schemas.explanation import ExplanationPayload
from app.services.explanation.db_utils import (
    verify_lecture_exists,
    explanation_exists,
    get_slide_context,
    save_explanation,
    increment_progress_and_check_completion,
)
from app.services.explanation.openai_utils import (
    generate_explanation,
    mock_generate_explanation,
)
from app.services.explanation.s3_utils import download_slide_image
from app.services.explanation.pubsub_utils import publish_summary_job
from app.utils.config import Settings


settings = Settings()


async def process_explanation_job(payload: ExplanationPayload):
    """
    Orchestrates the entire process of generating an explanation for a single slide.
    """
    logging.info(
        f"Received explanation request for slide {payload.slide_number} of lecture {payload.lecture_id}"
    )

    # Destructure payload for easier reference
    lecture_id = payload.lecture_id
    slide_id = payload.slide_id
    slide_number = payload.slide_number
    total_slides = payload.total_slides
    slide_image_path = payload.slide_image_path
    customer_identifier = payload.customer_identifier
    name = payload.name
    email = payload.email

    # Initialize connections
    if not settings.postgres_dsn:
        raise ValueError("POSTGRES_DSN is not configured.")
    conn = await asyncpg.connect(settings.postgres_dsn, statement_cache_size=0)

    s3_client = boto3.client(
        "s3",
        aws_access_key_id=settings.s3_access_key or None,
        aws_secret_access_key=settings.s3_secret_key or None,
        endpoint_url=settings.s3_endpoint_url or None,
    )

    try:
        # 1. Verify lecture exists
        if not await verify_lecture_exists(conn, lecture_id):
            logging.warning(f"Lecture {lecture_id} not found. Acknowledging message.")
            return

        # 2. Check if explanation already exists to ensure idempotency
        if await explanation_exists(conn, slide_id):
            logging.info(f"Explanation for slide {slide_id} already exists. Skipping.")
            return

        # 3. Download slide image from S3
        image_bytes = download_slide_image(
            s3_client, settings.s3_bucket_name, slide_image_path
        )

        # 4. Gather context (previous and next slide text)
        prev_text, next_text = await get_slide_context(conn, lecture_id, slide_number)

        # 5. Call the AI Professor
        if settings.mock_llm_calls:
            result, metadata = mock_generate_explanation(
                image_bytes,
                slide_number,
                total_slides,
                prev_text,
                next_text,
                str(lecture_id),
                str(slide_id),
            )
        else:
            result, metadata = await generate_explanation(
                image_bytes,
                slide_number,
                total_slides,
                prev_text,
                next_text,
                str(lecture_id),
                str(slide_id),
                customer_identifier,
                name,
                email,
            )

        # 6. Save the structured response
        await save_explanation(
            conn,
            slide_id,
            lecture_id,
            slide_number,
            result,
            metadata,
        )
        logging.info(f"Saved explanation for slide {slide_id}")

        # 7. Update progress and trigger summary if it was the last slide
        is_complete = await increment_progress_and_check_completion(conn, lecture_id)

        if is_complete:
            logging.info(
                f"Lecture {lecture_id} processing complete. Publishing summary job."
            )
            publish_summary_job(
                lecture_id,
                customer_identifier,
                name,
                email,
            )

    except Exception as e:
        logging.error(
            f"Error processing explanation for slide {slide_id}: {e}",
            exc_info=True,
        )
        if conn:
            error_info = {
                "service": "explanation",
                "slide_id": str(slide_id),
                "error": str(e),
            }
            await conn.execute(
                "UPDATE lectures SET explanation_error_details = $1::jsonb, updated_at = NOW() WHERE id = $2",
                json.dumps(error_info),
                lecture_id,
            )
        raise
    finally:
        await conn.close()
