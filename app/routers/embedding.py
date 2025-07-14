import logging

from fastapi import APIRouter, HTTPException, status, Depends

from app.schemas.common import PubSubRequest
from app.schemas.embedding import EmbeddingPayload
from app.services.embedding.orchestrator import process_embedding_job
from app.utils.auth import verify_token


router = APIRouter(
    prefix="/embedding",
    tags=["embedding"],
    dependencies=[Depends(verify_token)],
)


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
async def handle_embedding_job(request: PubSubRequest):
    """Handles an embedding job request from Pub/Sub."""
    try:
        payload = EmbeddingPayload(**request.message.data)
        logging.info(f"Processing embedding job for lecture_id: {payload.lecture_id}")
        await process_embedding_job(lecture_id=payload.lecture_id)
    except Exception as e:
        logging.error(f"Embedding job failed: {e}", exc_info=True)
        # Re-raise as an HTTPException to ensure Pub/Sub receives a failure response
        # and can attempt a retry, which is crucial for resilient systems.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process embedding job: {e}",
        )
