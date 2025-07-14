from uuid import UUID
from pydantic import BaseModel, Field


class SummaryPayload(BaseModel):
    """
    Represents the data expected in a Pub/Sub message for a summary request.
    """

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
