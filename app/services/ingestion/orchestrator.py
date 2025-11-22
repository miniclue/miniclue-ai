import logging
from typing import Dict
from app.schemas.ingestion import IngestionPayload

import asyncpg
import boto3
import pymupdf
import json

from app.services.ingestion.db_utils import (
    get_or_create_chunk,
    get_or_create_slide,
    set_lecture_parsing,
    update_lecture_sub_image_count,
    update_lecture_status,
    get_slides_with_images_for_lecture,
    verify_lecture_exists,
)
from app.services.ingestion.image_processing import (
    process_slide_sub_images,
    render_and_upload_slide_image,
)
from app.services.ingestion.s3_utils import download_pdf
from app.services.ingestion.text_processing import chunk_text_by_tokens
from app.services.ingestion.pubsub_utils import (
    publish_embedding_job,
    publish_explanation_job,
    publish_image_analysis_job,
)
from app.utils.config import Settings


settings = Settings()


async def ingest(
    payload: IngestionPayload,
):
    lecture_id = payload.lecture_id
    storage_path = payload.storage_path
    customer_identifier = payload.customer_identifier
    name = payload.name
    email = payload.email
    """
    Ingestion and Dispatch Workflow:
    - Parses a PDF into slides, text chunks, and images.
    - Uploads unique images to S3.
    - Dispatches jobs for image analysis and slide explanations via Pub/Sub.
    - Does NOT make any external AI calls.
    """

    logging.info(
        f"📋 Ingestion parameters - Lecture ID: {lecture_id}, "
        f"Storage Path: {storage_path}, Customer: {customer_identifier}"
    )

    if not settings.postgres_dsn:
        logging.error("❌ Postgres DSN not configured")
        raise RuntimeError("Postgres DSN not configured")

    conn = None
    doc = None
    s3_client = None
    try:
        logging.info("🔌 Connecting to S3 and PostgreSQL...")
        s3_client = boto3.client(
            "s3",
            aws_access_key_id=settings.s3_access_key or None,
            aws_secret_access_key=settings.s3_secret_key or None,
            endpoint_url=settings.s3_endpoint_url or None,
        )
        conn = await asyncpg.connect(settings.postgres_dsn, statement_cache_size=0)
        logging.info("✅ Connected to S3 and PostgreSQL")

        # Verify the lecture exists before proceeding (Defensive Subscriber)
        logging.info(f"🔍 Verifying lecture {lecture_id} exists...")
        if not await verify_lecture_exists(conn, lecture_id):
            logging.warning(
                f"⚠️ Lecture with ID {lecture_id} not found. Acknowledging message and stopping."
            )
            return
        logging.info(f"✅ Lecture {lecture_id} verified")

        # Clear any previous search-track errors since we're starting fresh
        logging.info("🧹 Clearing previous error details...")
        await conn.execute(
            "UPDATE lectures SET search_error_details = NULL, explanation_error_details = NULL WHERE id = $1",
            lecture_id,
        )

        logging.info(f"📥 Downloading PDF from S3: {storage_path}")
        pdf_bytes = download_pdf(s3_client, settings.s3_bucket_name, storage_path)
        logging.info(f"✅ PDF downloaded ({len(pdf_bytes)} bytes)")

        logging.info("📄 Opening PDF document...")
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        total_slides = doc.page_count
        logging.info(f"✅ PDF opened - Total slides: {total_slides}")

        logging.info(
            f"📊 Setting lecture status to parsing with {total_slides} slides..."
        )
        await set_lecture_parsing(conn, lecture_id, total_slides)

        logging.info(f"🔄 Processing {total_slides} slides...")
        processed_images_map: Dict[str, str] = {}
        image_analysis_jobs = []
        for page_index in range(total_slides):
            slide_number = page_index + 1
            page = doc.load_page(page_index)

            async with conn.transaction():
                raw_text = page.get_text("text")
                slide_id = await get_or_create_slide(
                    conn, lecture_id, slide_number, raw_text
                )

                # 2. Create text chunks
                chunks = chunk_text_by_tokens(raw_text)
                for idx, (text_chunk, token_count) in enumerate(chunks):
                    await get_or_create_chunk(
                        conn,
                        slide_id,
                        lecture_id,
                        slide_number,
                        idx,
                        text_chunk,
                        token_count,
                    )

                # 3. Render and process images
                await render_and_upload_slide_image(
                    doc, s3_client, conn, page_index, lecture_id, slide_id
                )
                new_jobs = await process_slide_sub_images(
                    doc,
                    s3_client,
                    conn,
                    page_index,
                    lecture_id,
                    slide_id,
                    processed_images_map,
                )
                image_analysis_jobs.extend(new_jobs)

        # Post-loop operations
        logging.info("📊 Finalizing slide processing...")
        total_sub_images = len(processed_images_map)
        logging.info(f"✅ Processed {total_sub_images} unique sub-images")
        await update_lecture_sub_image_count(conn, lecture_id, total_sub_images)

        # Set status to 'explaining' BEFORE publishing jobs to prevent race conditions
        logging.info("📤 Setting lecture status to 'explaining'...")
        await update_lecture_status(conn, lecture_id, "explaining")

        # Dispatch explanation jobs for every slide
        logging.info("📨 Dispatching explanation jobs...")
        slides_for_jobs = await get_slides_with_images_for_lecture(conn, lecture_id)
        logging.info(f"📋 Found {len(slides_for_jobs)} slides for explanation jobs")
        for slide_record in slides_for_jobs:
            slide_image_path = slide_record["slide_image_path"]
            if slide_image_path:
                publish_explanation_job(
                    lecture_id=lecture_id,
                    slide_id=slide_record["id"],
                    slide_number=slide_record["slide_number"],
                    total_slides=total_slides,
                    slide_image_path=slide_image_path,
                    customer_identifier=customer_identifier,
                    name=name,
                    email=email,
                )
            else:
                logging.warning(
                    f"Could not find full slide image for slide_id {slide_record['id']}. Skipping explanation job."
                )

        if total_sub_images > 0:
            logging.info(
                f"📨 Dispatching {len(image_analysis_jobs)} image analysis jobs..."
            )
            for job in image_analysis_jobs:
                publish_image_analysis_job(
                    slide_image_id=job["slide_image_id"],
                    lecture_id=job["lecture_id"],
                    image_hash=job["image_hash"],
                    customer_identifier=customer_identifier,
                    name=name,
                    email=email,
                )
            logging.info("✅ Image analysis jobs dispatched")
        else:
            logging.info("📨 No sub-images found, dispatching embedding job...")
            publish_embedding_job(
                lecture_id,
                customer_identifier=customer_identifier,
                name=name,
                email=email,
            )
            logging.info("✅ Embedding job dispatched")

        logging.info(
            f"🎉 Ingestion workflow completed successfully for lecture {lecture_id}"
        )
        # Finalize - status is already set above

    except Exception as e:
        logging.error(
            f"❌ Ingestion failed for lecture {lecture_id}: {e}", exc_info=True
        )
        if conn:
            error_info = {"service": "ingestion", "error": str(e)}
            logging.info(f"📝 Updating lecture {lecture_id} status to 'failed'...")
            await update_lecture_status(
                conn, lecture_id, "failed", search_error_details=json.dumps(error_info)
            )
        raise
    finally:
        logging.info("🧹 Cleaning up resources...")
        if doc:
            doc.close()
        if s3_client:
            s3_client.close()
        if conn:
            await conn.close()
        logging.info("✅ Resources cleaned up")
