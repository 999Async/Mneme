from datetime import datetime

from pydantic import BaseModel, Field


class Tags(BaseModel):
    keywords: list[str] = []
    entities: dict = {}


class CreateMemoryReq(BaseModel):
    scope: str = Field(..., pattern="^(personal|group)$")
    owner_id: str
    type: str = Field(..., pattern="^(decision|fact|intent|command)$")
    content: str
    context_snapshot: str | None = None
    tags: Tags = Field(default_factory=Tags)
    source_message_id: str | None = None
    source_chat_id: str | None = None
    confidence: float = 1.0
    idempotency_key: str | None = None


class MemoryResp(BaseModel):
    id: str
    scope: str
    owner_id: str
    type: str
    content: str
    context_snapshot: str | None = None
    tags: Tags
    strength: float
    active: bool
    version: int
    parent_id: str | None = None
    superseded_by: str | None = None
    last_reviewed_at: datetime | None = None
    source_message_id: str | None = None
    source_chat_id: str | None = None
    confidence: float | None = None
    created_at: datetime
    updated_at: datetime


class MemoryListResp(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[MemoryResp]


class ReviewReq(BaseModel):
    action: str = Field(..., pattern="^(review|dismiss|done)$")
    user_id: str
