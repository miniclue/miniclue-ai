import logging

from fastapi import APIRouter, HTTPException, status, Depends

from app.schemas.common import PubSubRequest
from app.schemas.summary import SummaryPayload
from app.services.summary.orchestrator import process_summary_job
from app.utils.auth import verify_token


router = APIRouter(
    prefix="/summary",
    tags=["summary"],
    dependencies=[Depends(verify_token)],
)


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
async def handle_summary_job(request: PubSubRequest):
    """Handles a summary job request from Pub/Sub."""
    try:
        payload = SummaryPayload(**request.message.data)
        await process_summary_job(payload)

    except Exception as e:
        logging.error(f"Summary job failed: {e}", exc_info=True)
        # Re-raise as an HTTPException to signal a server-side error to Pub/Sub,
        # which will trigger a retry. The dead-letter queue is the final backstop.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process summary job: {e}",
        )
