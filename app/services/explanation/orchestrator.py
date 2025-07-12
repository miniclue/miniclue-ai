import logging
from uuid import UUID

import asyncpg

from app.services.explanation.db_utils import (
    fetch_context,
    fetch_image_data,
    fetch_slide_text,
    fetch_related_concepts,
    persist_explanation_and_update_progress,
)
from app.services.explanation.openai_utils import (
    generate_explanation,
    mock_generate_explanation,
)
from app.services.embedding.openai_utils import get_embedding, mock_get_embedding
from app.utils.config import Settings

settings = Settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s:     %(message)s",
)


async def explain(slide_id: UUID, lecture_id: UUID, slide_number: int):
    """
    Consume explanation job: generate explanation for a slide, persist results,
    update lecture progress, and enqueue summary when complete.
    """
    logging.info(
        f"Starting explanation for slide_id={slide_id}, slide_number={slide_number}"
    )
    if not settings.postgres_dsn:
        logging.error("Postgres DSN not configured")
        raise RuntimeError("Postgres DSN not configured")
    if not settings.xai_api_key:
        logging.error("XAI API key not configured")
        raise RuntimeError("XAI API key not configured")

    conn = await asyncpg.connect(settings.postgres_dsn)
    try:
        context_recap, previous_one_liner = await fetch_context(
            conn, lecture_id, slide_number
        )
        full_text = await fetch_slide_text(conn, slide_id)

        # Fetch related concepts (partial context) via vector similarity
        if settings.mock_llm_calls:
            vector_str, _ = mock_get_embedding(full_text)
        else:
            vector_str, _ = get_embedding(full_text)

        related_concepts = await fetch_related_concepts(
            conn,
            lecture_id,
            slide_number,
            vector_str,
            2,
        )
        ocr_texts, alt_texts = await fetch_image_data(conn, lecture_id, slide_number)

        if settings.mock_llm_calls:
            slide_type, one_liner, content, metadata_str = mock_generate_explanation(
                slide_number,
                context_recap,
                previous_one_liner,
                full_text,
                related_concepts,
                ocr_texts,
                alt_texts,
            )
        else:
            slide_type, one_liner, content, metadata_str = generate_explanation(
                slide_number,
                context_recap,
                previous_one_liner,
                full_text,
                related_concepts,
                ocr_texts,
                alt_texts,
            )

        await persist_explanation_and_update_progress(
            conn,
            slide_id,
            lecture_id,
            slide_number,
            one_liner,
            content,
            slide_type,
            metadata_str,
        )

        logging.info(f"Finished explanation for slide_id={slide_id}")
    except (ValueError, asyncpg.PostgresError) as e:
        logging.error(f"Explanation failed for slide_id={slide_id}: {e}")
        raise
    finally:
        if conn and not conn.is_closed():
            await conn.close()
            logging.info("Postgres connection closed for explanation")
