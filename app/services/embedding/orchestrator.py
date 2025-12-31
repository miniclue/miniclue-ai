import logging
from collections import defaultdict
import json

import asyncpg

from app.services.embedding import db_utils
from app.schemas.embedding import EmbeddingPayload
from app.utils import embedding_utils
from app.utils import llm_utils
from app.utils.config import Settings

settings = Settings()


async def process_embedding_job(payload: EmbeddingPayload):
    lecture_id = payload.lecture_id
    """
    Orchestrates the embedding process for an entire lecture.
    """
    if not settings.postgres_dsn:
        logging.error("Postgres DSN not configured")
        raise RuntimeError("Postgres DSN not configured")

    conn = None
    try:
        conn = await asyncpg.connect(settings.postgres_dsn, statement_cache_size=0)
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
                # DEPRECATED: Rendezvous point logic removed
                await db_utils.set_embeddings_complete(conn, lecture_id)
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
                    texts_to_join.append(f"OCR Text: {image['ocr_text']}")
                if image["alt_text"]:
                    texts_to_join.append(f"Alt Text: {image['alt_text']}")

            enriched_text = " ".join(texts_to_join).strip()
            enriched_texts.append(enriched_text)

        # 4. Fetch user API context (required)
        embedding_client, _ = await llm_utils.get_llm_context(
            payload.customer_identifier, settings.embedding_model, is_embedding=True
        )

        # 5. Generate embeddings in a batch, capturing metadata
        embedding_results, metadata = embedding_utils.generate_embeddings(
            texts=enriched_texts,
            lecture_id=str(lecture_id),
            user_id=payload.customer_identifier,
            client=embedding_client,
        )

        # 5. Prepare data for batch database insertion, ensuring result count matches inputs
        if len(embedding_results) != len(chunks):
            msg = (
                f"Expected {len(chunks)} embeddings but got {len(embedding_results)} "
                f"results for lecture {lecture_id}"
            )
            logging.error(msg)
            raise RuntimeError(msg)
        # Convert common metadata to JSON string for storage
        metadata_json = json.dumps(metadata) if metadata else "{}"
        embeddings_to_insert = []
        # Pair up each chunk with its corresponding result (skipping unmatched)
        for chunk, result in zip(chunks, embedding_results):
            embeddings_to_insert.append(
                {
                    "chunk_id": chunk["id"],
                    "slide_id": chunk["slide_id"],
                    "lecture_id": lecture_id,
                    "slide_number": chunk["slide_number"],
                    "vector": result["vector"],
                    "metadata": metadata_json,
                }
            )
        # Warn for any chunks without a result
        if len(embedding_results) < len(chunks):
            for missing_chunk in chunks[len(embedding_results) :]:
                logging.warning(
                    f"No embedding result for chunk {missing_chunk['id']} in lecture {lecture_id}, skipping."
                )

        # 6. Save vectors and finalize the process in a single transaction
        async with conn.transaction():
            await db_utils.batch_upsert_embeddings(conn, embeddings_to_insert)

            # DEPRECATED: Rendezvous point with explanation/summary track removed
            # We now directly set status to 'complete' once embeddings are finished
            await db_utils.set_embeddings_complete(conn, lecture_id)
            await db_utils.set_lecture_status_to_complete(conn, lecture_id)

    except Exception as e:
        logging.error(
            f"Error processing embedding for lecture {lecture_id}: {e}", exc_info=True
        )
        # Optionally, update lecture status to 'failed'
        if conn:
            error_info = {"service": "embedding", "error": str(e)}
            await conn.execute(
                "UPDATE lectures SET status = 'failed', embedding_error_details = $1::jsonb WHERE id = $2",
                json.dumps(error_info),
                lecture_id,
            )
        raise
    finally:
        await conn.close()
