"""L1/L2 推送候选筛选 + 防抖 + 日上限"""

from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Memory, PushLog


async def run_l1_push(db: AsyncSession) -> dict:
    """L1 推送执行

    筛选 strength <= 阈值的记忆，按 strength 升序排列，
    每个目标每天最多 3 条，工作日 09:00-19:00 推送。
    """
    threshold = settings.push_l1_threshold
    daily_limit = settings.push_l1_daily_limit
    window_start = settings.push_window_start
    window_end = settings.push_window_end

    # 检查推送时间窗口
    now = datetime.utcnow()  # Note: 实际应为北京时间
    hour = now.hour
    if not (window_start <= hour < window_end):
        return {"pushed": 0, "push_cards": [], "reason": "outside push window"}

    # 查询 strength 低于阈值的活跃记忆
    stmt = (
        select(Memory)
        .where(Memory.active == True, Memory.strength <= threshold)
        .order_by(Memory.strength.asc())
    )
    result = await db.execute(stmt.limit(50))
    candidates = list(result.scalars().all())

    push_cards = []
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    for memory in candidates:
        if len(push_cards) >= 20:  # 总量限制
            break

        target_id = memory.source_chat_id or memory.owner_id
        if not target_id:
            continue

        # 检查日上限
        count_stmt = select(func.count()).select_from(PushLog).where(
            PushLog.target_id == target_id,
            PushLog.push_type == "L1",
            PushLog.created_at >= today_start,
        )
        daily_count = (await db.execute(count_stmt)).scalar() or 0
        if daily_count >= daily_limit:
            continue

        # 检查 24h 防抖
        from datetime import timedelta
        since_24h = now - timedelta(hours=24)
        debounce_stmt = select(func.count()).select_from(PushLog).where(
            PushLog.memory_id == memory.id,
            PushLog.target_id == target_id,
            PushLog.created_at >= since_24h,
        )
        debounce_count = (await db.execute(debounce_stmt)).scalar() or 0
        if debounce_count > 0:
            continue

        # 生成推送卡片
        card = {
            "target_id": target_id,
            "content": _build_l1_card(memory),
        }
        push_cards.append(card)

        # 写 push_log
        log = PushLog(
            memory_id=memory.id,
            push_type="L1",
            target_id=target_id,
        )
        db.add(log)

    await db.commit()

    # TODO: 调 feishu_push_service 发送卡片
    return {"pushed": len(push_cards), "push_cards": push_cards}


async def find_l2_candidates(
    db: AsyncSession, *, message: str, chat_id: str, threshold: float = 0.75
) -> list[dict]:
    """查找 L2 候选记忆（对话语义相似度触发）"""
    # Demo 简化：用子串匹配模拟语义相似度
    stmt = select(Memory).where(
        Memory.active == True,
        Memory.source_chat_id == chat_id,
        Memory.content.ilike(f"%{message[:20]}%"),
    ).limit(3)
    result = await db.execute(stmt)
    candidates = list(result.scalars().all())

    # 检查 24h 防抖
    from datetime import timedelta
    now = datetime.utcnow()
    since_24h = now - timedelta(hours=24)
    filtered = []
    for m in candidates:
        debounce_stmt = select(func.count()).select_from(PushLog).where(
            PushLog.memory_id == m.id,
            PushLog.target_id == chat_id,
            PushLog.push_type == "L2",
            PushLog.created_at >= since_24h,
        )
        count = (await db.execute(debounce_stmt)).scalar() or 0
        if count == 0:
            filtered.append({
                "id": m.id,
                "content": m.content,
                "type": m.type,
                "created_at": m.created_at.isoformat(),
            })
            # 写 push_log
            db.add(PushLog(memory_id=m.id, push_type="L2", target_id=chat_id))

    await db.commit()
    return filtered


def _build_l1_card(memory: Memory) -> dict:
    """构建 L1 飞书卡片 JSON"""
    return {
        "type": memory.type,
        "content": memory.content,
        "source": f"群聊 | {memory.created_at.strftime('%m月%d日')}",
    }
