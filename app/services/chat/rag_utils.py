import logging
from uuid import UUID
from typing import List, Dict, Any

import asyncpg

from app.services.chat import db_utils
from app.services.embedding import llm_utils
from app.utils.config import Settings

settings = Settings()


async def generate_query_embedding(
    text: str, user_api_key: str, user_id: str, chat_id: str
) -> List[float]:
    """
    Create embedding for user query.
    """
    results, _ = await llm_utils.generate_embeddings(
        texts=[text],
        lecture_or_chat_id=chat_id,
        user_id=user_id,
        user_api_key=user_api_key,
    )

    if not results:
        raise ValueError("Failed to generate query embedding")

    import json

    vector_str = results[0]["vector"]
    return json.loads(vector_str)


async def retrieve_relevant_chunks(
    conn: asyncpg.Connection,
    lecture_id: UUID,
    chat_id: str,
    query_text: str,
    user_api_key: str,
    user_id: str,
    top_k: int = 5,
) -> List[Dict[str, Any]]:
    """
    Full RAG pipeline: generate query embedding and retrieve relevant chunks.
    Returns list of chunk texts with metadata (slide_number, chunk_index).
    """
    # Generate query embedding
    query_embedding = await generate_query_embedding(
        query_text, user_api_key, user_id, chat_id
    )

    # Query similar embeddings
    similar_embeddings = await db_utils.query_similar_embeddings(
        conn, lecture_id, query_embedding, limit=top_k
    )

    if not similar_embeddings:
        logging.warning(f"No similar embeddings found for lecture {lecture_id}")
        return []

    # Get chunk IDs
    chunk_ids = [UUID(str(row["chunk_id"])) for row in similar_embeddings]

    # Get full chunk context with OCR and alt text
    chunks = await db_utils.get_chunk_context(conn, chunk_ids)

    # Build enriched text blocks
    results = []
    for chunk in chunks:
        text_parts = [chunk["text"]]
        if chunk["ocr_text"]:
            text_parts.append(f"OCR Text: {chunk['ocr_text']}\n")
        if chunk["alt_text"]:
            text_parts.append(f"Alt Text: {chunk['alt_text']}")

        enriched_text = " ".join(text_parts).strip()

        results.append(
            {
                "text": enriched_text,
                "slide_number": chunk["slide_number"],
                "chunk_index": chunk["chunk_index"],
            }
        )

    return results
