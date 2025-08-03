import json
import random
from typing import List, Dict, Any, Tuple

from app.utils.config import Settings
from app.utils.posthog_client import posthog_openai_client

settings = Settings()


async def generate_embeddings(
    texts: List[str], lecture_id: str, customer_identifier: str
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Generate embedding vectors for a batch of text chunks.
    """
    if not texts:
        return [], {}

    response = await posthog_openai_client.embeddings.create(
        model=settings.embedding_model,
        input=texts,
        posthog_distinct_id=customer_identifier,
        posthog_trace_id=lecture_id,
        posthog_properties={
            "service": "embedding",
            "lecture_id": lecture_id,
            "texts_count": len(texts),
        },
    )

    # Batch-level metadata
    common_metadata: Dict[str, Any] = {
        "model": response.model,
        "usage": response.usage.model_dump(),
    }
    results: List[Dict[str, Any]] = []
    for data in response.data:
        vector_str = json.dumps(data.embedding)
        # Store an empty object for per-item metadata to avoid redundancy
        results.append({"vector": vector_str, "metadata": json.dumps({})})
    return results, common_metadata


def mock_generate_embeddings(
    texts: List[str], lecture_id: str
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Mock embedding function for development.
    Returns a list of fake embedding vectors and metadata.
    """
    if not texts:
        return [], {}

    # Generate mock results and aggregate usage
    total_prompt = 0
    results: List[Dict[str, Any]] = []
    for text in texts:
        tokens = len(text.split())
        total_prompt += tokens
        fake_vector = [random.uniform(-1, 1) for _ in range(1536)]
        vector_str = json.dumps(fake_vector)
        # Per-item metadata for DB is an empty object
        results.append(
            {
                "vector": vector_str,
                "metadata": json.dumps({}),
            }
        )
    # Batch-level metadata
    common_metadata = {
        "model": "mock-embedding-model",
        "usage": {
            "prompt_tokens": total_prompt,
            "completion_tokens": 0,
            "total_tokens": total_prompt,
        },
        "mock": True,
    }
    return results, common_metadata
