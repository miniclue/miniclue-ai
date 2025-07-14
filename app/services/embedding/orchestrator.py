import logging
from uuid import UUID
from collections import defaultdict
import json

import asyncpg

from app.services.embedding import db_utils
from app.services.embedding import openai_utils
from app.utils.config import Settings

settings = Settings()


async def process_embedding_job(lecture_id: UUID):
    """
    Orchestrates the embedding process for an entire lecture.
    """
    logging.info(f"Starting embedding process for lecture_id={lecture_id}")
    if not settings.postgres_dsn:
        logging.error("Postgres DSN not configured")
        raise RuntimeError("Postgres DSN not configured")

    conn = await asyncpg.connect(settings.postgres_dsn)
    try:
        # 1. Verify the lecture exists and is in a valid state
        if not await db_utils.verify_lecture_exists(conn, lecture_id):
            logging.warning(
                f"Lecture {lecture_id} not found or in terminal state. Acknowledging message."
            )
            return

        # 2. Get all chunks and content-rich images for the lecture
        chunks = await db_utils.get_lecture_chunks(conn, lecture_id)
        images = await db_utils.get_content_images_for_lecture(conn, lecture_id)

        if not chunks:
            logging.warning(
                f"No chunks found for lecture {lecture_id}. Nothing to embed."
            )
            # Even if no chunks, we should mark embeddings as complete to unblock the pipeline
            async with conn.transaction():
                current_status = await db_utils.set_embeddings_complete(
                    conn, lecture_id
                )
                if current_status == "summarising":
                    await db_utils.set_lecture_status_to_complete(conn, lecture_id)
            return

        # 3. Enrich the text for each chunk
        image_info_by_slide = defaultdict(list)
        for image in images:
            image_info_by_slide[image["slide_id"]].append(image)

        enriched_texts = []
        for chunk in chunks:
            texts_to_join = [chunk["text"]]
            for image in image_info_by_slide[chunk["slide_id"]]:
                if image["ocr_text"]:
                    texts_to_join.append(f'OCR Text: {image["ocr_text"]}')
                if image["alt_text"]:
                    texts_to_join.append(f'Alt Text: {image["alt_text"]}')

            enriched_text = " ".join(texts_to_join).strip()
            enriched_texts.append(enriched_text)

        # 4. Generate embeddings in a batch
        if settings.mock_llm_calls:
            embedding_results = openai_utils.mock_generate_embeddings(enriched_texts)
        else:
            embedding_results = await openai_utils.generate_embeddings(enriched_texts)

        # 5. Prepare data for batch database insertion
        embeddings_to_insert = []
        for i, chunk in enumerate(chunks):
            result = embedding_results[i]
            embeddings_to_insert.append(
                {
                    "chunk_id": chunk["id"],
                    "slide_id": chunk["slide_id"],
                    "lecture_id": lecture_id,
                    "slide_number": chunk["slide_number"],
                    "vector": result["vector"],
                    "metadata": result["metadata"],
                }
            )

        # 6. Save vectors and finalize the process in a single transaction
        async with conn.transaction():
            await db_utils.batch_upsert_embeddings(conn, embeddings_to_insert)

            # Atomically update status and check if the other track is done
            current_status = await db_utils.set_embeddings_complete(conn, lecture_id)
            if current_status == "summarising":
                await db_utils.set_lecture_status_to_complete(conn, lecture_id)

        logging.info(f"Finished embedding process for lecture_id={lecture_id}")

    except Exception as e:
        logging.error(
            f"Error processing embedding for lecture {lecture_id}: {e}", exc_info=True
        )
        # Optionally, update lecture status to 'failed'
        if conn:
            error_info = {"service": "embedding", "error": str(e)}
            await conn.execute(
                "UPDATE lectures SET status = 'failed', search_error_details = $1::jsonb WHERE id = $2",
                json.dumps(error_info),
                lecture_id,
            )
        raise
    finally:
        await conn.close()
        logging.info(f"Postgres connection closed for embedding lecture {lecture_id}")
