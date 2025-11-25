import logging

from fastapi import APIRouter, HTTPException, status, Depends

from app.schemas.common import PubSubRequest
from app.schemas.image_analysis import ImageAnalysisPayload
from app.services.image_analysis.orchestrator import process_image_analysis_job
from app.utils.auth import verify_token
from app.utils.secret_manager import InvalidAPIKeyError


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
        await process_image_analysis_job(payload)
    except InvalidAPIKeyError as e:
        # Permanent error: acknowledge message to stop Pub/Sub retries
        logging.error(f"Invalid API key for image analysis job: {e}")
        return  # Return 204 to acknowledge the message
    except Exception as e:
        logging.error(f"Image analysis job failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process image analysis job: {e}",
        )
