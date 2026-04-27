from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Index, String, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.models.memory import Base, UuidStr


class ContextSnapshot(Base):
    __tablename__ = "context_snapshots"

    id: Mapped[str] = mapped_column(UuidStr, primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    chat_id: Mapped[str] = mapped_column(String(100), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_snapshots_user", "user_id", created_at.desc()),
    )
