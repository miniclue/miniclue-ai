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


class SlidePurpose(str, Enum):
    cover = "cover"
    header = "header"
    content = "content"
    error = "error"


class ExplanationResult(BaseModel):
    slide_purpose: SlidePurpose = Field(
        ..., description="The structural role of this slide."
    )
    explanation: str = Field(
        ..., description="The lecture slide explanation in Markdown format with LaTeX."
    )
