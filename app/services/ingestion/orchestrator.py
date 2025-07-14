from uuid import UUID
import logging
from typing import Dict

import asyncpg
import boto3
import pymupdf

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

logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(message)s")


async def ingest(lecture_id: UUID, storage_path: str):
    """
    Ingestion and Dispatch Workflow:
    - Parses a PDF into slides, text chunks, and images.
    - Uploads unique images to S3.
    - Dispatches jobs for image analysis and slide explanations via Pub/Sub.
    - Does NOT make any external AI calls.
    """
    logging.info(
        f"Starting ingestion for lecture_id={lecture_id}, storage_path={storage_path}"
    )

    if not settings.postgres_dsn:
        logging.error("Postgres DSN not configured")
        raise RuntimeError("Postgres DSN not configured")

    s3_client = boto3.client(
        "s3",
        aws_access_key_id=settings.s3_access_key or None,
        aws_secret_access_key=settings.s3_secret_key or None,
        endpoint_url=settings.s3_endpoint_url or None,
    )

    conn = None
    try:
        conn = await asyncpg.connect(settings.postgres_dsn)
        logging.info("Postgres connection established")

        # Verify the lecture exists before proceeding (Defensive Subscriber)
        if not await verify_lecture_exists(conn, lecture_id):
            logging.warning(
                f"Lecture with ID {lecture_id} not found. Acknowledging message and stopping."
            )
            return

        pdf_bytes = download_pdf(s3_client, settings.s3_bucket_name, storage_path)
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        total_slides = doc.page_count
        logging.info(f"PDF opened, total slides: {total_slides}")

        await set_lecture_parsing(conn, lecture_id, total_slides)
        logging.info(f"Lecture {lecture_id} status set to 'parsing'")

        processed_images_map: Dict[str, str] = {}
        image_analysis_jobs = []
        for page_index in range(total_slides):
            slide_number = page_index + 1
            page = doc.load_page(page_index)
            logging.info(f"Processing slide {slide_number}/{total_slides}")

            async with conn.transaction():
                raw_text = page.get_text("text")
                slide_id = await get_or_create_slide(
                    conn, lecture_id, slide_number, raw_text
                )

                # 2. Create text chunks
                chunks = chunk_text_by_tokens(raw_text)
                logging.info(f"Slide {slide_number}: Created {len(chunks)} text chunks")
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
        total_sub_images = len(processed_images_map)
        await update_lecture_sub_image_count(conn, lecture_id, total_sub_images)
        logging.info(f"Found {total_sub_images} unique sub-images in total.")

        # Dispatch explanation jobs for every slide
        slides_for_jobs = await get_slides_with_images_for_lecture(conn, lecture_id)
        logging.info(f"Dispatching {len(slides_for_jobs)} explanation jobs...")
        for slide_record in slides_for_jobs:
            slide_image_path = slide_record["slide_image_path"]
            if slide_image_path:
                publish_explanation_job(
                    lecture_id=lecture_id,
                    slide_id=slide_record["id"],
                    slide_number=slide_record["slide_number"],
                    total_slides=total_slides,
                    slide_image_path=slide_image_path,
                )
            else:
                logging.warning(
                    f"Could not find full slide image for slide_id {slide_record['id']}. Skipping explanation job."
                )

        if total_sub_images > 0:
            logging.info(
                f"Dispatching {len(image_analysis_jobs)} image analysis jobs..."
            )
            for job in image_analysis_jobs:
                publish_image_analysis_job(
                    slide_image_id=job["slide_image_id"],
                    lecture_id=job["lecture_id"],
                    image_hash=job["image_hash"],
                )
        else:
            logging.info("No sub-images found, dispatching embedding job directly.")
            publish_embedding_job(lecture_id)

        # Finalize
        await update_lecture_status(conn, lecture_id, "explaining")
        logging.info(f"Lecture {lecture_id} status updated to 'explaining'.")

    except Exception as e:
        logging.error(f"Ingestion failed for lecture {lecture_id}: {e}", exc_info=True)
        if conn:
            await update_lecture_status(conn, lecture_id, "failed")
        raise
    finally:
        if conn:
            await conn.close()
            logging.info("Postgres connection closed")

    logging.info(f"Finished ingestion for lecture_id={lecture_id}")
