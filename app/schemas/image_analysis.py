from enum import Enum
from uuid import UUID
from pydantic import BaseModel, Field
from typing import Optional


class ImageAnalysisPayload(BaseModel):
    slide_image_id: UUID
    lecture_id: UUID
    image_hash: str
    customer_identifier: str
    name: Optional[str] = None
    email: Optional[str] = None


class ImageAnalysisResult(BaseModel):
    class ImageType(str, Enum):
        content = "content"
        decorative = "decorative"

    image_type: ImageType = Field(
        ...,
        alias="type",
        description="The type of the image, e.g., 'content' or 'decorative'.",
    )
    ocr_text: str = Field(..., description="The extracted OCR text from the image.")
    alt_text: str = Field(..., description="A descriptive alt text for the image.")

    class Config:
        allow_population_by_field_name = True
