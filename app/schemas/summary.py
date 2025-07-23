from uuid import UUID
from pydantic import BaseModel, Field
from typing import Optional


class SummaryPayload(BaseModel):
    """
    Represents the data expected in a Pub/Sub message for a summary request.
    """

    customer_identifier: str
    name: Optional[str] = None
    email: Optional[str] = None

    lecture_id: UUID = Field(
        ..., description="The unique identifier for the lecture to be summarized."
    )


class Summary(BaseModel):
    """
    Represents a finalized summary record.
    """

    lecture_id: UUID
    content: str

    class Config:
        from_attributes = True
