import json
import logging
import random

from openai import OpenAI

from app.utils.config import Settings

settings = Settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)

client = OpenAI(api_key=settings.openai_api_key, base_url=settings.openai_api_base_url)


def get_embedding(text: str) -> tuple[str, str]:
    """
    Generate embedding vector for a text chunk.
    """
    response = client.embeddings.create(
        model=settings.embedding_model,
        input=text,
    )
    vector = response.data[0].embedding
    vector_str = json.dumps(vector)

    metadata = {}
    if hasattr(response, "model"):
        metadata["model"] = response.model
    usage = getattr(response, "usage", None)
    if usage is not None:
        metadata["prompt_tokens"] = getattr(usage, "prompt_tokens", None)
        metadata["total_tokens"] = getattr(usage, "total_tokens", None)
    metadata_str = json.dumps(metadata)

    return vector_str, metadata_str


def mock_get_embedding(text: str) -> tuple[str, str]:
    """
    Mock embedding function for development purposes.
    Returns a fake embedding vector and metadata.
    """
    # Generate a fake embedding vector with 1536 dimensions (typical for OpenAI embeddings)
    fake_vector = [random.uniform(-1, 1) for _ in range(1536)]
    vector_str = json.dumps(fake_vector)

    # Generate fake metadata
    metadata = {
        "model": "text-embedding-ada-002",
        "prompt_tokens": len(text.split()),
        "total_tokens": len(text.split()),
        "mock": True,
    }
    metadata_str = json.dumps(metadata)

    logging.info(f"Mock embedding generated for text of length {len(text)}")

    return vector_str, metadata_str
