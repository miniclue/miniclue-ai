import logging
import asyncpg
import boto3
import json

from app.schemas.explanation import ExplanationPayload, ExplanationResult
from app.services.explanation.db_utils import (
    verify_lecture_exists,
    explanation_exists,
    save_explanation,
    increment_progress_and_check_completion,
)
from app.services.explanation.llm_utils import (
    generate_explanation,
)
from app.services.explanation.s3_utils import download_slide_image
from app.services.explanation.pubsub_utils import publish_summary_job
from app.utils.config import Settings
from app.utils.sanitize import sanitize_json, sanitize_text
from app.utils.secret_manager import (
    get_user_api_key,
    SecretNotFoundError,
    InvalidAPIKeyError,
)


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
        "error": sanitize_text(str(error_message)) or "",
        "server_info": {"server_pid": conn.get_server_pid()},
    }

    # Get existing error details and append new error
    existing_errors = await conn.fetchval(
        "SELECT explanation_error_details FROM lectures WHERE id = $1",
        lecture_id,
    )

    # Normalize existing_errors to a Python list
    error_list = []
    if existing_errors:
        try:
            if isinstance(existing_errors, str):
                existing_obj = json.loads(existing_errors)
            else:
                existing_obj = existing_errors
        except Exception:
            existing_obj = None

        if isinstance(existing_obj, list):
            error_list = existing_obj
        elif isinstance(existing_obj, dict):
            error_list = [existing_obj]
        else:
            error_list = []

    error_list.append(error_info)

    # Ensure JSON is safe for JSONB and store as text to cast to jsonb
    safe_error_list = sanitize_json(error_list)
    await conn.execute(
        "UPDATE lectures SET explanation_error_details = $1::jsonb, updated_at = NOW() WHERE id = $2",
        json.dumps(safe_error_list),
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

    # Initialize resources
    conn = None
    s3_client = None
    image_bytes = None

    if not settings.postgres_dsn:
        raise ValueError("POSTGRES_DSN is not configured.")

    try:
        conn = await asyncpg.connect(settings.postgres_dsn, statement_cache_size=0)

        s3_client = boto3.client(
            "s3",
            aws_access_key_id=settings.s3_access_key or None,
            aws_secret_access_key=settings.s3_secret_key or None,
            endpoint_url=settings.s3_endpoint_url or None,
        )

        # 1. Verify lecture exists
        if not await verify_lecture_exists(conn, lecture_id):
            logging.warning(f"Lecture {lecture_id} not found. Acknowledging message.")
            return

        # 2. Check if explanation already exists to ensure idempotency
        if await explanation_exists(conn, slide_id):
            return

        # 3. Download slide image from S3
        image_bytes = download_slide_image(
            s3_client, settings.s3_bucket_name, slide_image_path
        )

        # 4. Fetch user OpenAI API key from Secret Manager (required)
        try:
            user_api_key = get_user_api_key(customer_identifier, provider="openai")
        except SecretNotFoundError:
            logging.error(f"API key not found for user {customer_identifier}")
            await _record_explanation_error(
                conn, lecture_id, slide_id, Exception("User API key not found")
            )
            raise InvalidAPIKeyError(
                "User API key not found. Please configure your API key in settings."
            )
        except Exception as e:
            logging.error(
                f"Failed to fetch user API key for {customer_identifier}: {e}"
            )
            await _record_explanation_error(
                conn,
                lecture_id,
                slide_id,
                Exception(f"Failed to access API key: {str(e)}"),
            )
            raise InvalidAPIKeyError(f"Failed to access API key: {str(e)}")

        # 5. Call LLM
        try:
            result, metadata = await generate_explanation(
                image_bytes,
                slide_number,
                total_slides,
                str(lecture_id),
                str(slide_id),
                customer_identifier,
                user_api_key,
                name,
                email,
            )

        except Exception as e:
            # For any error (timeout, empty response, network, etc.), create a
            # fallback explanation so the slide is populated and progress can proceed.
            logging.error(f"LLM call failed for slide {slide_id}: {e}", exc_info=True)

            # Record the error for auditing
            try:
                await _record_explanation_error(conn, lecture_id, slide_id, e)
            except Exception:
                logging.error("Failed to record explanation error to DB", exc_info=True)

            # Create minimal fallback for unexpected exceptions
            result = ExplanationResult(
                explanation="Unable to generate explanation due to technical difficulties. Please try again.",
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

        # 7. Save the structured response
        await save_explanation(
            conn,
            slide_id,
            lecture_id,
            slide_number,
            result,
            metadata,
        )

        # 8. Update progress and trigger summary if it was the last slide
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
        # Clean up resources explicitly to prevent memory leaks
        if image_bytes is not None:
            del image_bytes
        if s3_client:
            s3_client.close()
        if conn:
            await conn.close()
