import logging

from fastapi import APIRouter, HTTPException, status

from app.schemas.common import PubSubRequest
from app.schemas.image_analysis import ImageAnalysisPayload

# TODO: Implement the image analysis service orchestrator
# from app.services.image_analysis.orchestrator import process_image_analysis_job

router = APIRouter(prefix="/image-analysis", tags=["image-analysis"])


@router.post("/", status_code=status.HTTP_204_NO_CONTENT)
async def handle_image_analysis_job(request: PubSubRequest):
    """Handles an image analysis job request from Pub/Sub."""
    try:
        payload = ImageAnalysisPayload(**request.message.data)
        logging.info(
            f"Received image analysis job for image_hash: {payload.image_hash}"
        )
        # await process_image_analysis_job(
        #     slide_image_id=payload.slide_image_id,
        #     lecture_id=payload.lecture_id,
        #     image_hash=payload.image_hash,
        # )
        logging.warning("Placeholder: process_image_analysis_job not implemented.")
    except Exception as e:
        logging.error(f"Image analysis job failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process image analysis job: {e}",
        )
