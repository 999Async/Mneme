from uuid import uuid4

from sqlalchemy import BigInteger, Boolean, Float, ForeignKey, Index, Integer, String, Text, JSON
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from pgvector.sqlalchemy import Vector

from app.utils.datetime import ms_now


class Base(DeclarativeBase):
    pass


# JSON on both SQLite and PostgreSQL
JsonType = JSON

# UUID: String(36) for SQLite compatibility
UuidStr = String(36)


class Memory(Base):
    __tablename__ = "memories"

    id: Mapped[str] = mapped_column(UuidStr, primary_key=True, default=lambda: str(uuid4()))
    scope: Mapped[str] = mapped_column(String(10), nullable=False)  # personal / group
    owner_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(20), nullable=False)  # decision / fact / intent / command
    content: Mapped[str] = mapped_column(Text, nullable=False)
    attachments: Mapped[dict] = mapped_column(JsonType, nullable=False, default=lambda: {"urls": []})
    context_snapshot: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    tags: Mapped[dict] = mapped_column(JsonType, nullable=False, default=lambda: {"keywords": [], "entities": {}})
    strength: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    decay_params: Mapped[dict] = mapped_column(
        JsonType, nullable=False,
        default=lambda: {"base_decay_rate": 0.5, "personal_sensitivity": 1.0},
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    parent_id: Mapped[str | None] = mapped_column(UuidStr, ForeignKey("memories.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    superseded_by: Mapped[str | None] = mapped_column(UuidStr, ForeignKey("memories.id"), nullable=True)
    last_reviewed_at: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    source_message_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_chat_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    rep_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # 重复提及次数
    embedding = mapped_column(Vector(1536), nullable=True)
    created_at: Mapped[int] = mapped_column(BigInteger, nullable=False, default=ms_now)
    updated_at: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=ms_now, onupdate=ms_now
    )

    __table_args__ = (
        # Composite: covers search/conflict/list WHERE owner_id=X AND scope=X AND active=True
        # PostgreSQL uses leftmost prefix: (owner_id), (owner_id, scope) also benefit
        Index("idx_memories_owner_scope_active", "owner_id", "scope", "active"),
        # Decay batch: WHERE active=True ORDER BY updated_at ASC
        Index("idx_memories_active_updated", "active", "updated_at"),
        # Push candidates: WHERE active=True AND strength <= X ORDER BY strength ASC
        Index("idx_memories_active_strength", "active", "strength"),
        # Version chain traversal
        Index("idx_memories_parent", "parent_id"),
    )
