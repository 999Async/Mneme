from pydantic import BaseModel


class CreateSnapshotReq(BaseModel):
    user_id: str
    chat_id: str
    snapshot: str  # JSON string
    expires_in_minutes: int = 30


class SnapshotResp(BaseModel):
    id: str
    user_id: str
    chat_id: str
    snapshot: str
    created_at: int
    expires_at: int
