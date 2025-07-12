from uuid import UUID
from pydantic import BaseModel


class IngestionPayload(BaseModel):
    lecture_id: UUID
    storage_path: str
