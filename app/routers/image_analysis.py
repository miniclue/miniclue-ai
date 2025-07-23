import logging

from fastapi import APIRouter, HTTPException, status, Depends

from app.schemas.common import PubSubRequest
from app.schemas.image_analysis import ImageAnalysisPayload
from app.services.image_analysis.orchestrator import process_image_analysis_job
from app.utils.auth import verify_token


router = APIRouter(
    prefix="/image-analysis",
    tags=["image-analysis"],
    dependencies=[Depends(verify_token)],
)


@router.post("", status_code=status.HTTP_204_NO_CONTENT)
async def handle_image_analysis_job(request: PubSubRequest):
    """Handles an image analysis job request from Pub/Sub."""
    try:
        payload = ImageAnalysisPayload(**request.message.data)
        logging.info(
            f"Processing image analysis job for image_hash: {payload.image_hash}"
        )
        await process_image_analysis_job(payload)
    except Exception as e:
        logging.error(f"Image analysis job failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process image analysis job: {e}",
        )
