import logging

from fastapi import APIRouter, HTTPException, status
from app.schemas.common import PubSubRequest
from app.schemas.embedding import EmbeddingPayload

# TODO: Implement the new embedding service orchestrator
# from app.services.embed.orchestrator import process_embedding_job


router = APIRouter(prefix="/embedding", tags=["embedding"])


@router.post("/", status_code=status.HTTP_204_NO_CONTENT)
async def handle_embedding_job(request: PubSubRequest):
    """Handles an embedding job request from Pub/Sub."""
    try:
        payload = EmbeddingPayload(**request.message.data)
        logging.info(f"Received embedding job for lecture_id: {payload.lecture_id}")
        # await process_embedding_job(lecture_id=payload.lecture_id)
        logging.warning("Placeholder: process_embedding_job not implemented.")
    except Exception as e:
        logging.error(f"Embedding job failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process embedding job: {e}",
        )
