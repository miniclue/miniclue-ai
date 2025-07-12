from uuid import UUID
from pydantic import BaseModel


class ImageAnalysisPayload(BaseModel):
    slide_image_id: UUID
    lecture_id: UUID
    image_hash: str
