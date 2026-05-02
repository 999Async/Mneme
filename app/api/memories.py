from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.common import ok
from app.schemas.memory import CreateMemoryReq, MemoryListResp, MemoryResp
from app.services.conflict_service import detect_conflicts
from app.services.memory_service import create_memory, get_memory, list_memories, soft_delete, supersede_memory

router = APIRouter()


@router.post("")
async def create_memory_api(body: CreateMemoryReq, db: AsyncSession = Depends(get_db)):
    """创建记忆（自动抽取 + 显式存储共用）"""
    # 1. 去重检测（优先于冲突检测）
    from app.services.conflict_service import detect_duplicates
    dupes = await detect_duplicates(
        db, content=body.content, owner_id=body.owner_id, scope=body.scope,
    )
    if dupes:
        return ok({
            "id": dupes[0]["id"],
            "scope": body.scope,
            "type": body.type,
            "content": dupes[0]["content"],
            "duplicate": True,
            "duplicate_reason": dupes[0]["duplicate_reason"],
            "message": "记忆已存在，未创建新记录",
        })

    # 2. 冲突检测（现在返回 conflicts 和 duplicates）
    from app.services.conflict_service import detect_conflicts_with_llm
    conflict_result = await detect_conflicts_with_llm(
        db, content=body.content, tags=body.tags.model_dump(),
        owner_id=body.owner_id, scope=body.scope,
    )

    conflicts = conflict_result.get("conflicts", [])
    llm_duplicates = conflict_result.get("duplicates", [])

    # 3. LLM 判定的重复
    if llm_duplicates:
        return ok({
            "id": llm_duplicates[0]["id"],
            "scope": body.scope,
            "type": body.type,
            "content": llm_duplicates[0]["content"],
            "duplicate": True,
            "duplicate_reason": llm_duplicates[0].get("llm_reason", "LLM 判定为重复"),
            "message": "记忆已存在，未创建新记录",
        })

    # 4. 确定版本链
    parent_id = None
    version = 1
    if conflicts:
        old_id = conflicts[0]["id"]
        old = await get_memory(db, old_id)
        if old:
            parent_id = old.id
            version = old.version + 1

    # 5. 创建记忆
    memory = await create_memory(
        db, scope=body.scope, owner_id=body.owner_id, type=body.type,
        content=body.content, tags=body.tags.model_dump(),
        confidence=body.confidence, parent_id=parent_id,
        source_message_id=body.source_message_id, source_chat_id=body.source_chat_id,
    )
    memory.version = version
    db.add(memory)

    # 6. 覆写旧记忆
    overwritten_id = None
    if conflicts:
        old = await get_memory(db, conflicts[0]["id"])
        if old:
            await supersede_memory(db, old, memory.id)
            overwritten_id = old.id

    await db.commit()
    await db.refresh(memory)

    return ok({
        "id": memory.id,
        "scope": memory.scope,
        "type": memory.type,
        "content": memory.content,
        "strength": memory.strength,
        "active": memory.active,
        "version": memory.version,
        "parent_id": memory.parent_id,
        "created_at": memory.created_at,
        "conflict_detected": len(conflicts) > 0,
        "overwritten_id": overwritten_id,
    })


@router.get("")
async def list_memories_api(
    owner_id: str = Query(...),
    scope: str | None = None,
    type: str | None = None,
    keywords: str | None = None,
    active_only: bool = True,
    page: int = 1,
    page_size: int = 20,
    db: AsyncSession = Depends(get_db),
):
    """查询记忆（关键词搜索）"""
    if keywords:
        from app.services.search_service import search_memories
        results = await search_memories(db, query=keywords, owner_id=owner_id,
                                        scope=scope, limit=page_size)
        return ok({
            "total": len(results), "page": 1, "page_size": page_size,
            "items": [MemoryResp(
                id=m.id, scope=m.scope, owner_id=m.owner_id, type=m.type,
                content=m.content, tags=m.tags, strength=m.strength,
                active=m.active, version=m.version, parent_id=m.parent_id,
                created_at=m.created_at, updated_at=m.updated_at,
            ).model_dump() for m in results],
        })

    items, total = await list_memories(
        db, owner_id=owner_id, scope=scope, type=type,
        active_only=active_only, page=page, page_size=page_size,
    )
    return ok({
        "total": total, "page": page, "page_size": page_size,
        "items": [MemoryResp(
            id=m.id, scope=m.scope, owner_id=m.owner_id, type=m.type,
            content=m.content, tags=m.tags, strength=m.strength,
            active=m.active, version=m.version, parent_id=m.parent_id,
            last_reviewed_at=m.last_reviewed_at,
            source_message_id=m.source_message_id,
            confidence=m.confidence,
            created_at=m.created_at, updated_at=m.updated_at,
        ).model_dump() for m in items],
    })


@router.delete("/{memory_id}")
async def delete_memory_api(memory_id: str, user_id: str = "", db: AsyncSession = Depends(get_db)):
    """软删除记忆"""
    success = await soft_delete(db, memory_id)
    if not success:
        return {"ok": False, "error": "Memory not found", "code": 1002, "data": None}
    return ok(None)
