"""遗忘曲线衰减计算 + 批量更新"""

import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Memory, MemoryLog
from app.utils.datetime import ms_now, MS_24H

# 按记忆类型的分层衰减率（越小 = 衰减越慢 = 保留越久）
TYPE_DECAY_RATES: dict[str, float] = {
    "fact": 0.3,       # 事实性信息，衰减慢
    "decision": 0.4,   # 决策，中等
    "command": 0.5,    # 指令性，默认
    "intent": 0.6,     # 意图性，衰减快
}


async def run_decay(db: AsyncSession, batch_size: int = 100) -> dict:
    """执行衰减扫描

    公式：strength(t) = e^(-rate × sensitivity × t_days)
    rate 按 memory.type 分层，未知类型 fallback 到 base_rate
    跳过 strength=0 和近期更新的记忆
    """
    base_rate = settings.decay_base_rate
    sensitivity = settings.decay_sensitivity
    threshold = settings.push_l1_threshold

    now = ms_now()
    # 只处理1小时前更新的、strength > 0 的记忆
    cutoff = now - (MS_24H // 24)

    query = (
        select(Memory)
        .where(
            Memory.active == True,
            Memory.strength > 0,
            Memory.updated_at < cutoff,
        )
        .order_by(Memory.updated_at.asc())
    )
    result = await db.execute(query.limit(batch_size))
    memories = list(result.scalars().all())

    scanned = 0
    decayed = 0
    push_candidates = 0

    for memory in memories:
        scanned += 1
        ref_ms = memory.last_reviewed_at or memory.created_at
        days_since = (now - ref_ms) / MS_24H

        rate = TYPE_DECAY_RATES.get(memory.type, base_rate)
        new_strength = math.exp(-rate * sensitivity * days_since)
        new_strength = round(max(new_strength, 0.0), 4)

        if abs(new_strength - memory.strength) > 0.001:
            decayed += 1
            old_strength = memory.strength
            memory.strength = new_strength
            memory.updated_at = now

            if new_strength <= threshold:
                push_candidates += 1

            # 写 log
            log = MemoryLog(
                memory_id=memory.id,
                action="decay",
                detail={
                    "strength_before": round(old_strength, 4),
                    "strength_after": new_strength,
                    "interval_days": round(days_since, 2),
                    "decay_rate": rate,
                    "memory_type": memory.type,
                },
            )
            db.add(log)

    await db.commit()

    return {
        "scanned": scanned,
        "decayed": decayed,
        "triggered_push_candidates": push_candidates,
    }
