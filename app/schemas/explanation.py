from uuid import UUID
from pydantic import BaseModel


class ExplanationPayload(BaseModel):
    lecture_id: UUID
    slide_id: UUID
    slide_number: int
    total_slides: int
    slide_image_path: str
