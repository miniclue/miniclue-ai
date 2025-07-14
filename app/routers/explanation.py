import logging

from fastapi import APIRouter, HTTPException, status, Depends

from app.schemas.common import PubSubRequest
from app.schemas.explanation import ExplanationPayload
from app.services.explanation.orchestrator import process_explanation_job
from app.utils.auth import verify_token


router = APIRouter(
    prefix="/explanation",
    tags=["explanation"],
    dependencies=[Depends(verify_token)],
)


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
async def handle_explanation_job(request: PubSubRequest):
    """Handles an explanation job request from Pub/Sub."""
    try:
        payload = ExplanationPayload(**request.message.data)
        logging.info(f"Processing explanation job for slide: {payload.slide_number}")
        await process_explanation_job(payload)
    except Exception as e:
        logging.error(f"Explanation job failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process explanation job: {e}",
        )
