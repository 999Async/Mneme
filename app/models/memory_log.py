from uuid import uuid4

from sqlalchemy import BigInteger, ForeignKey, Index, String, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.memory import Base, UuidStr
from app.utils.datetime import ms_now


class MemoryLog(Base):
    __tablename__ = "memory_logs"

    id: Mapped[str] = mapped_column(UuidStr, primary_key=True, default=lambda: str(uuid4()))
    memory_id: Mapped[str] = mapped_column(
        UuidStr, ForeignKey("memories.id", ondelete="CASCADE"), nullable=False
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # create / review / decay / overwrite / forget
    detail: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=ms_now)

    __table_args__ = (
        Index("idx_memory_logs_memory", "memory_id", created_at.desc()),
    )
