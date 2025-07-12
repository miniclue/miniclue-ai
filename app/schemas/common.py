import base64
import json
from pydantic import BaseModel, Field, field_validator


class PubSubMessage(BaseModel):
    data: dict = Field(..., alias="data")

    @field_validator("data", mode="before")
    @classmethod
    def decode_and_parse_data(cls, v):
        """Decodes base64 and parses the inner JSON."""
        if isinstance(v, str):
            decoded_bytes = base64.b64decode(v)
            decoded_str = decoded_bytes.decode("utf-8")
            return json.loads(decoded_str)
        return v


class PubSubRequest(BaseModel):
    message: PubSubMessage = Field(..., alias="message")
    subscription: str = Field(..., alias="subscription")
