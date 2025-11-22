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
    logging.info(
        f"📥 Received ingestion request from subscription: {request.subscription}"
    )
    logging.debug(f"Request message data: {request.message.data}")

    try:
        logging.info("Parsing ingestion payload...")
        payload = IngestionPayload(**request.message.data)
        logging.info(
            f"✅ Payload parsed successfully - Lecture ID: {payload.lecture_id}, "
            f"Storage Path: {payload.storage_path}, Customer: {payload.customer_identifier}"
        )

        logging.info(f"🚀 Starting ingestion process for lecture {payload.lecture_id}")
        await ingest(payload)
        logging.info(
            f"✅ Ingestion completed successfully for lecture {payload.lecture_id}"
        )
    except Exception as e:
        logging.error(f"❌ Ingestion job failed: {e}", exc_info=True)
        # Re-raise to be caught by the global exception handler
        # This ensures the message is not acknowledged and will be redelivered.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process ingestion job: {e}",
        )
