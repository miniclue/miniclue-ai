from uuid import UUID
from pydantic import BaseModel


class SummaryPayload(BaseModel):
    lecture_id: UUID
