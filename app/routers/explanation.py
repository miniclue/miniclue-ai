import logging

from fastapi import APIRouter, HTTPException, status

from app.schemas.common import PubSubRequest
from app.schemas.explanation import ExplanationPayload

# TODO: Implement the new explanation service orchestrator
# from app.services.explain.orchestrator import process_explanation_job

router = APIRouter(prefix="/explanation", tags=["explanation"])


@router.post("/", status_code=status.HTTP_204_NO_CONTENT)
async def handle_explanation_job(request: PubSubRequest):
    """Handles an explanation job request from Pub/Sub."""
    try:
        payload = ExplanationPayload(**request.message.data)
        logging.info(f"Received explanation job for slide_id: {payload.slide_id}")
        # await process_explanation_job(
        #     lecture_id=payload.lecture_id,
        #     slide_id=payload.slide_id,
        #     slide_number=payload.slide_number,
        #     total_slides=payload.total_slides,
        #     slide_image_path=payload.slide_image_path,
        # )
        logging.warning("Placeholder: process_explanation_job not implemented.")
    except Exception as e:
        logging.error(f"Explanation job failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process explanation job: {e}",
        )
