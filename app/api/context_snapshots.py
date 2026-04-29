from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.common import ok
from app.schemas.context_snapshot import CreateSnapshotReq
from app.models import ContextSnapshot
from app.utils.datetime import ms_now

router = APIRouter()


@router.post("")
async def create_snapshot(body: CreateSnapshotReq, db: AsyncSession = Depends(get_db)):
    """保存上下文快照"""
    expires_at = ms_now() + body.expires_in_minutes * 60 * 1000
    snapshot = ContextSnapshot(
        user_id=body.user_id,
        chat_id=body.chat_id,
        snapshot=body.snapshot,
        expires_at=expires_at,
    )
    db.add(snapshot)
    await db.commit()
    await db.refresh(snapshot)
    return ok({
        "id": snapshot.id,
        "user_id": snapshot.user_id,
        "expires_at": snapshot.expires_at,
    })


@router.get("/latest")
async def get_latest_snapshot(user_id: str = Query(...), db: AsyncSession = Depends(get_db)):
    """获取最新上下文快照"""
    from sqlalchemy import select
    now = ms_now()
    stmt = (
        select(ContextSnapshot)
        .where(ContextSnapshot.user_id == user_id, ContextSnapshot.expires_at > now)
        .order_by(ContextSnapshot.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    snapshot = result.scalar_one_or_none()
    if not snapshot:
        return ok(None)
    return ok({
        "id": snapshot.id, "user_id": snapshot.user_id,
        "chat_id": snapshot.chat_id, "snapshot": snapshot.snapshot,
        "created_at": snapshot.created_at,
        "expires_at": snapshot.expires_at,
    })


@router.delete("/latest")
async def delete_latest_snapshot(user_id: str = Query(...), db: AsyncSession = Depends(get_db)):
    """清除最新上下文快照"""
    from sqlalchemy import select, delete
    stmt = (
        delete(ContextSnapshot)
        .where(ContextSnapshot.user_id == user_id)
        .where(ContextSnapshot.id == (
            select(ContextSnapshot.id)
            .where(ContextSnapshot.user_id == user_id)
            .order_by(ContextSnapshot.created_at.desc())
            .limit(1)
        ))
    )
    await db.execute(stmt)
    await db.commit()
    return ok(None)
