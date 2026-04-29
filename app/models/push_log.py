from uuid import uuid4

from sqlalchemy import BigInteger, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.memory import Base, UuidStr
from app.utils.datetime import ms_now


class PushLog(Base):
    __tablename__ = "push_logs"

    id: Mapped[str] = mapped_column(UuidStr, primary_key=True, default=lambda: str(uuid4()))
    memory_id: Mapped[str] = mapped_column(UuidStr, ForeignKey("memories.id"), nullable=False)
    push_type: Mapped[str] = mapped_column(String(3), nullable=False)  # L0 / L1 / L2
    target_id: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=ms_now)

    __table_args__ = (
        Index("idx_push_logs_target", "target_id", "push_type", created_at.desc()),
    )
