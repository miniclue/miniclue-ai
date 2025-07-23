import json
import logging
import random
from typing import List, Dict, Any

from openai import AsyncOpenAI

from app.utils.config import Settings

settings = Settings()

client = AsyncOpenAI(
    api_key=settings.openai_api_key,
    base_url=settings.openai_api_base_url,
)


async def generate_embeddings(
    texts: List[str], lecture_id: str
) -> List[Dict[str, Any]]:
    """
    Generate embedding vectors for a batch of text chunks.
    """
    if not texts:
        return []

    response = await client.embeddings.create(
        model=settings.embedding_model,
        input=texts,
        extra_body={
            "metadata": {
                "environment": settings.app_env,
                "service": "embedding",
                "lecture_id": lecture_id,
            }
        },
    )

    results = []
    for data in response.data:
        vector_str = json.dumps(data.embedding)
        metadata = {
            "model": response.model,
            "usage": response.usage.model_dump(),
        }
        results.append({"vector": vector_str, "metadata": json.dumps(metadata)})

    return results


def mock_generate_embeddings(texts: List[str], lecture_id: str) -> List[Dict[str, Any]]:
    """
    Mock embedding function for development.
    Returns a list of fake embedding vectors and metadata.
    """
    if not texts:
        return []

    results = []
    for text in texts:
        fake_vector = [random.uniform(-1, 1) for _ in range(1536)]
        vector_str = json.dumps(fake_vector)
        metadata = {
            "model": "mock-embedding-model",
            "prompt_tokens": len(text.split()),
            "total_tokens": len(text.split()),
            "mock": True,
            "text": text,
        }
        results.append({"vector": vector_str, "metadata": json.dumps(metadata)})

    logging.info(
        f"Mock embeddings generated for {len(texts)} texts (lecture_id={lecture_id})."
    )
    return results
