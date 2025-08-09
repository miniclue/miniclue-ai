from enum import Enum
from uuid import UUID
from pydantic import BaseModel, Field
from typing import Optional


class ExplanationPayload(BaseModel):
    lecture_id: UUID
    slide_id: UUID
    slide_number: int
    total_slides: int
    slide_image_path: str
    customer_identifier: str
    name: Optional[str] = None
    email: Optional[str] = None


class ExplanationResult(BaseModel):
    explanation: str = Field(
        ..., description="Detailed explanation of the slide's content."
    )
    one_liner: str = Field(..., description="A one-sentence summary of the slide.")

    class SlidePurpose(str, Enum):
        cover = "cover"
        header = "header"
        content = "content"
        error = "error"

    slide_purpose: SlidePurpose = Field(
        ..., description="The purpose of the slide in the context of the presentation."
    )
