from uuid import UUID
from pydantic import BaseModel


class EmbeddingPayload(BaseModel):
    lecture_id: UUID
