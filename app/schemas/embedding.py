from uuid import UUID
from pydantic import BaseModel
from typing import Optional


class EmbeddingPayload(BaseModel):
    lecture_id: UUID
    customer_identifier: str
    name: Optional[str] = None
    email: Optional[str] = None
