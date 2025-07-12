import logging

from fastapi import APIRouter, HTTPException, status

from app.schemas.common import PubSubRequest
from app.schemas.summary import SummaryPayload

# TODO: Implement the new summary service orchestrator
# from app.services.summarize.orchestrator import process_summary_job

router = APIRouter(prefix="/summary", tags=["summary"])


@router.post("/", status_code=status.HTTP_204_NO_CONTENT)
async def handle_summary_job(request: PubSubRequest):
    """Handles a summary job request from Pub/Sub."""
    try:
        payload = SummaryPayload(**request.message.data)
        logging.info(f"Received summary job for lecture_id: {payload.lecture_id}")
        # await process_summary_job(lecture_id=payload.lecture_id)
        logging.warning("Placeholder: process_summary_job not implemented.")
    except Exception as e:
        logging.error(f"Summary job failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process summary job: {e}",
        )
