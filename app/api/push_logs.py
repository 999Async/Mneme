from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.common import ok
from app.schemas.push_log import CreatePushLogReq
from app.models import PushLog
from app.utils.datetime import ms_now, ms_day_start, MS_24H

router = APIRouter()


@router.post("")
async def create_push_log(body: CreatePushLogReq, db: AsyncSession = Depends(get_db)):
    """记录推送历史"""
    log = PushLog(
        memory_id=body.memory_id,
        push_type=body.push_type,
        target_id=body.target_id,
        idempotency_key=body.idempotency_key,
    )
    db.add(log)
    await db.commit()
    return ok({"id": log.id})


@router.get("/check")
async def check_push_status(
    memory_id: str = Query(...),
    target_id: str = Query(...),
    push_type: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """检查推送防抖状态"""
    since = ms_now() - MS_24H
    stmt = select(func.count()).select_from(PushLog).where(
        PushLog.memory_id == memory_id,
        PushLog.target_id == target_id,
        PushLog.push_type == push_type,
        PushLog.created_at >= since,
    )
    count = (await db.execute(stmt)).scalar() or 0

    # L1: 检查今日总量
    if push_type == "L1":
        today_start = ms_day_start(ms_now())
        daily_stmt = select(func.count()).select_from(PushLog).where(
            PushLog.target_id == target_id,
            PushLog.push_type == "L1",
            PushLog.created_at >= today_start,
        )
        daily_count = (await db.execute(daily_stmt)).scalar() or 0
        return ok({"can_push": daily_count < 3, "daily_count": daily_count})

    # L2: 同一记忆对同一用户/群 24h 内最多一次
    return ok({"can_push": count == 0, "count_24h": count})
