import logging

from fastapi import APIRouter, HTTPException, status, Depends

from app.schemas.common import PubSubRequest
from app.schemas.ingestion import IngestionPayload
from app.services.ingestion.orchestrator import ingest
from app.utils.auth import verify_token

router = APIRouter(
    prefix="/ingestion",
    tags=["ingestion"],
    dependencies=[Depends(verify_token)],
)


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
async def handle_ingestion_job(request: PubSubRequest):
    """Handles an ingestion job request from Pub/Sub."""
    try:
        payload = IngestionPayload(**request.message.data)
        logging.info(f"Received ingestion job for lecture_id: {payload.lecture_id}")
        await ingest(lecture_id=payload.lecture_id, storage_path=payload.storage_path)
    except Exception as e:
        logging.error(f"Ingestion job failed: {e}", exc_info=True)
        # Re-raise to be caught by the global exception handler
        # This ensures the message is not acknowledged and will be redelivered.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process ingestion job: {e}",
        )
