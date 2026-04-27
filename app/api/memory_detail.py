from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.common import ok
from app.schemas.memory import ReviewReq
from app.services.memory_service import get_history, get_memory, review_memory

router = APIRouter()


@router.get("/{memory_id}")
async def get_memory_api(memory_id: str, db: AsyncSession = Depends(get_db)):
    """获取单条记忆详情"""
    memory = await get_memory(db, memory_id)
    if not memory:
        return {"ok": False, "error": "Memory not found", "code": 1002, "data": None}
    return ok({
        "id": memory.id, "scope": memory.scope, "owner_id": memory.owner_id,
        "type": memory.type, "content": memory.content,
        "context_snapshot": memory.context_snapshot,
        "tags": memory.tags, "strength": memory.strength,
        "active": memory.active, "version": memory.version,
        "parent_id": memory.parent_id,
        "last_reviewed_at": memory.last_reviewed_at.isoformat() if memory.last_reviewed_at else None,
        "created_at": memory.created_at.isoformat(),
        "updated_at": memory.updated_at.isoformat(),
    })


@router.get("/{memory_id}/history")
async def get_memory_history_api(memory_id: str, db: AsyncSession = Depends(get_db)):
    """获取记忆版本历史"""
    current = await get_memory(db, memory_id)
    if not current:
        return {"ok": False, "error": "Memory not found", "code": 1002, "data": None}

    history = await get_history(db, memory_id)
    return ok({
        "current": {
            "id": current.id, "content": current.content,
            "version": current.version, "active": current.active,
            "created_at": current.created_at.isoformat(),
        },
        "history": [{
            "id": h.id, "content": h.content,
            "version": h.version, "active": h.active,
            "created_at": h.created_at.isoformat(),
        } for h in history],
    })


@router.post("/{memory_id}/review")
async def review_memory_api(memory_id: str, body: ReviewReq, db: AsyncSession = Depends(get_db)):
    """复习记忆：review / dismiss / done"""
    memory = await review_memory(db, memory_id, body.action, body.user_id)
    if not memory:
        return {"ok": False, "error": "Memory not found", "code": 1002, "data": None}
    return ok({
        "id": memory.id,
        "strength": memory.strength,
        "last_reviewed_at": memory.last_reviewed_at.isoformat() if memory.last_reviewed_at else None,
        "active": memory.active,
    })
