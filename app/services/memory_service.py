"""记忆 CRUD + 版本链管理"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Memory, MemoryLog


async def create_memory(
    db: AsyncSession,
    *,
    scope: str,
    owner_id: str,
    type: str,
    content: str,
    tags: dict,
    confidence: float = 1.0,
    context_snapshot: str | None = None,
    source_message_id: str | None = None,
    source_chat_id: str | None = None,
    parent_id: str | None = None,
) -> Memory:
    """创建记忆"""
    memory = Memory(
        id=str(uuid4()),
        scope=scope,
        owner_id=owner_id,
        type=type,
        content=content,
        tags=tags,
        confidence=confidence,
        context_snapshot=context_snapshot,
        source_message_id=source_message_id,
        source_chat_id=source_chat_id,
        parent_id=parent_id,
    )
    db.add(memory)

    # 写 memory_log
    log = MemoryLog(
        memory_id=memory.id,
        action="create",
        detail={"source_message_id": source_message_id, "confidence": confidence},
    )
    db.add(log)
    await db.commit()
    await db.refresh(memory)
    return memory


async def get_memory(db: AsyncSession, memory_id: str) -> Memory | None:
    return await db.get(Memory, memory_id)


async def list_memories(
    db: AsyncSession,
    *,
    owner_id: str,
    scope: str | None = None,
    type: str | None = None,
    active_only: bool = True,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Memory], int]:
    """分页查询记忆"""
    query = select(Memory).where(Memory.owner_id == owner_id)

    if scope:
        query = query.where(Memory.scope == scope)
    if type:
        query = query.where(Memory.type == type)
    if active_only:
        query = query.where(Memory.active == True)

    # count
    count_q = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_q)).scalar() or 0

    # paginate
    query = query.order_by(Memory.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = list(result.scalars().all())

    return items, total


async def get_history(db: AsyncSession, memory_id: str) -> list[Memory]:
    """获取版本历史（沿 parent_id 链追溯）"""
    history = []
    current = await db.get(Memory, memory_id)
    if not current:
        return history

    # 沿 parent_id 往上追溯
    visited = set()
    node = current
    while node and node.parent_id and node.parent_id not in visited:
        visited.add(node.parent_id)
        parent = await db.get(Memory, node.parent_id)
        if parent:
            history.append(parent)
            node = parent
        else:
            break

    return history


async def supersede_memory(db: AsyncSession, old_memory: Memory, new_memory_id: str) -> None:
    """将旧记忆标记为被覆写"""
    old_memory.active = False
    old_memory.superseded_by = new_memory_id
    old_memory.updated_at = datetime.utcnow()

    log = MemoryLog(
        memory_id=old_memory.id,
        action="overwrite",
        detail={"overwritten_by": new_memory_id, "reason": "conflict detected"},
    )
    db.add(log)
    await db.commit()


async def soft_delete(db: AsyncSession, memory_id: str) -> bool:
    """软删除记忆"""
    memory = await db.get(Memory, memory_id)
    if not memory:
        return False
    memory.active = False
    memory.updated_at = datetime.utcnow()

    log = MemoryLog(memory_id=memory_id, action="forget", detail={"reason": "user delete"})
    db.add(log)
    await db.commit()
    return True


async def review_memory(
    db: AsyncSession, memory_id: str, action: str, user_id: str
) -> Memory | None:
    """复习记忆：review / dismiss / done"""
    memory = await db.get(Memory, memory_id)
    if not memory:
        return None

    now = datetime.utcnow()

    if action == "review":
        memory.strength = 1.0
        memory.last_reviewed_at = now
        log_action = "review"
        log_detail = {"action": "review", "user_id": user_id}
    elif action == "dismiss":
        memory.active = False
        log_action = "review"
        log_detail = {"action": "dismiss", "user_id": user_id}
    elif action == "done":
        memory.strength = min(memory.strength * 1.5, 1.0)
        memory.last_reviewed_at = now
        log_action = "review"
        log_detail = {"action": "done", "user_id": user_id}
    else:
        return None

    memory.updated_at = now
    log = MemoryLog(memory_id=memory_id, action=log_action, detail=log_detail)
    db.add(log)
    await db.commit()
    await db.refresh(memory)
    return memory
