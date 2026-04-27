from pydantic import BaseModel


class CreatePushLogReq(BaseModel):
    memory_id: str
    push_type: str  # L0 / L1 / L2
    target_id: str
    idempotency_key: str | None = None
