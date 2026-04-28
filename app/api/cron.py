from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.common import ok

router = APIRouter()


@router.post("/decay")
async def cron_decay(db: AsyncSession = Depends(get_db)):
    """遗忘曲线衰减扫描（每30分钟 Cron 触发）"""
    from app.services.decay_service import run_decay
    result = await run_decay(db)
    return ok(result)


@router.post("/push-l1")
async def cron_push_l1(db: AsyncSession = Depends(get_db)):
    """L1 推送执行"""
    from app.services.push_service import run_l1_push
    result = await run_l1_push(db)
    return ok(result)


@router.get("/push-l2/candidates")
async def get_l2_candidates(
    current_message: str = Query(...),
    current_snapshot: str = Query(""),
    chat_id: str = Query(...),
    threshold: float = 0.75,
    db: AsyncSession = Depends(get_db),
):
    """获取 L2 候选记忆"""
    from app.services.push_service import find_l2_candidates
    candidates = await find_l2_candidates(db, message=current_message, chat_id=chat_id, threshold=threshold)
    return ok({"candidates": candidates})


@router.post("/extract-buffers")
async def cron_extract_buffers(db: AsyncSession = Depends(get_db)):
    """定时扫描消息缓冲区，提取静默超时的缓冲（每5分钟 Cron 触发）"""
    from app.services.message_buffer_service import extract_all_buffers
    result = await extract_all_buffers(db)
    return ok(result)
