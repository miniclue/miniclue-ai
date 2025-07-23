from uuid import UUID
from pydantic import BaseModel
from typing import Optional


class IngestionPayload(BaseModel):
    lecture_id: UUID
    storage_path: str
    customer_identifier: str
    name: Optional[str] = None
    email: Optional[str] = None
