from pydantic import BaseModel
from uuid import UUID
from typing import List, Dict, Any


class MessagePart(BaseModel):
    type: str
    text: str | None = None


class ChatRequest(BaseModel):
    lecture_id: UUID
    chat_id: UUID
    user_id: UUID
    message: List[Dict[str, Any]]
    model: str


class ChatStreamChunk(BaseModel):
    content: str
    done: bool


class ChatTitleRequest(BaseModel):
    lecture_id: UUID
    chat_id: UUID
    user_id: UUID
    user_message: List[Dict[str, Any]]
    assistant_message: List[Dict[str, Any]]


class ChatTitleResponse(BaseModel):
    title: str
