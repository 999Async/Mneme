from pydantic import BaseModel


class ConflictDetectReq(BaseModel):
    content: str
    tags: dict
    owner_id: str
    scope: str
    exclude_id: str | None = None


class ConflictItem(BaseModel):
    id: str
    content: str
    similarity_score: float
    conflict_reason: str
    created_at: str


class ConflictDetectResp(BaseModel):
    has_conflict: bool
    conflicts: list[ConflictItem]
