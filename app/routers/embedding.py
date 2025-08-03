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
        await process_embedding_job(payload)

    except Exception as e:
        logging.error(f"Embedding job failed: {e}", exc_info=True)
        # Re-raise as an HTTPException to signal a server-side error to Pub/Sub,
        # which will trigger a retry. The dead-letter queue is the final backstop.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process embedding job: {e}",
        )
