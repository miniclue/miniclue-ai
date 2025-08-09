import logging
import asyncpg
import boto3
import json

from app.schemas.explanation import ExplanationPayload, ExplanationResult
from app.services.explanation.db_utils import (
    verify_lecture_exists,
    explanation_exists,
    get_slide_context,
    save_explanation,
    increment_progress_and_check_completion,
)
from app.services.explanation.llm_utils import (
    generate_explanation,
    mock_generate_explanation,
)
from app.services.explanation.s3_utils import download_slide_image
from app.services.explanation.pubsub_utils import publish_summary_job
from app.utils.config import Settings
from app.utils.llm_db_utils import log_llm_call, compute_cost


settings = Settings()


async def _record_explanation_error(
    conn: asyncpg.Connection,
    lecture_id,
    slide_id,
    error_message: str,
) -> None:
    """Record explanation error details into the lectures table, appending to existing errors."""
    error_info = {
        "service": "explanation",
        "slide_id": str(slide_id),
        "error": str(error_message),
        "server_info": json.dumps({"server_pid": conn.get_server_pid()}),
    }

    # Get existing error details and append new error
    existing_errors = await conn.fetchval(
        "SELECT explanation_error_details FROM lectures WHERE id = $1",
        lecture_id,
    )

    if existing_errors:
        # If existing errors exist, append to them
        if isinstance(existing_errors, list):
            error_list = existing_errors
        else:
            # If it's a single error object, convert to list
            error_list = [existing_errors]
        error_list.append(error_info)
    else:
        # If no existing errors, create new list
        error_list = [error_info]

    await conn.execute(
        "UPDATE lectures SET explanation_error_details = $1::jsonb, updated_at = NOW() WHERE id = $2",
        json.dumps(error_list),
        lecture_id,
    )


async def process_explanation_job(payload: ExplanationPayload):
    """
    Orchestrates the entire process of generating an explanation for a single slide.
    """
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

        # 5. Call LLM
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
            try:
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

            except Exception as e:
                # For any error (timeout, empty response, network, etc.), create a
                # fallback explanation so the slide is populated and progress can proceed.
                logging.error(
                    f"LLM call failed for slide {slide_id}: {e}", exc_info=True
                )

                # Record the error for auditing
                try:
                    await _record_explanation_error(conn, lecture_id, slide_id, e)
                except Exception:
                    logging.error(
                        "Failed to record explanation error to DB", exc_info=True
                    )

                # Create minimal fallback for unexpected exceptions
                result = ExplanationResult(
                    explanation="Unable to generate explanation due to technical difficulties. Please try again.",
                    one_liner="Technical error occurred during explanation generation.",
                    slide_purpose="error",
                )
                metadata = {
                    "model": settings.explanation_model,
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                    },
                    "response_id": None,
                    "fallback": True,
                    "fallback_reason": str(e),
                }

        # Log LLM call for explanation
        try:
            usage = metadata.get("usage") or {}
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)
            cost = compute_cost(
                settings.explanation_model, prompt_tokens, completion_tokens
            )
            await log_llm_call(
                conn,
                lecture_id,
                slide_id,
                "explanation",
                settings.explanation_model,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                cost,
                metadata,
            )
        except Exception:
            logging.error("Failed to log LLM call for explanation", exc_info=True)

        # 6. Save the structured response
        await save_explanation(
            conn,
            slide_id,
            lecture_id,
            slide_number,
            result,
            metadata,
        )

        # 7. Update progress and trigger summary if it was the last slide
        is_complete = await increment_progress_and_check_completion(conn, lecture_id)

        if is_complete:
            publish_summary_job(
                lecture_id,
                customer_identifier,
                name,
                email,
            )

    except Exception as e:
        logging.error(
            f"Explanation job failed for slide {slide_id} in lecture {lecture_id}: {e}",
            exc_info=True,
        )
        if conn:
            await _record_explanation_error(conn, lecture_id, slide_id, e)
        raise
    finally:
        await conn.close()
