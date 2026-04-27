"""遗忘曲线衰减计算 + 批量更新"""

import math
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Memory, MemoryLog


async def run_decay(db: AsyncSession, batch_size: int = 100) -> dict:
    """执行衰减扫描

    公式：strength(t) = e^(-base_decay_rate × personal_sensitivity × t_days)
    """
    base_rate = settings.decay_base_rate
    sensitivity = settings.decay_sensitivity
    threshold = settings.push_l1_threshold

    # 查询所有活跃记忆
    query = select(Memory).where(Memory.active == True).order_by(Memory.updated_at.asc())
    result = await db.execute(query.limit(batch_size))
    memories = list(result.scalars().all())

    now = datetime.utcnow()
    scanned = 0
    decayed = 0
    push_candidates = 0

    for memory in memories:
        scanned += 1
        ref_time = memory.last_reviewed_at or memory.created_at
        days_since = (now - ref_time).total_seconds() / 86400

        new_strength = math.exp(-base_rate * sensitivity * days_since)
        new_strength = round(max(new_strength, 0.0), 4)

        if abs(new_strength - memory.strength) > 0.001:
            decayed += 1
            memory.strength = new_strength
            memory.updated_at = now

            if new_strength <= threshold:
                push_candidates += 1

            # 写 log
            log = MemoryLog(
                memory_id=memory.id,
                action="decay",
                detail={
                    "strength_before": round(memory.strength, 4),
                    "strength_after": new_strength,
                    "interval_days": round(days_since, 2),
                },
            )
            db.add(log)

    await db.commit()

    return {
        "scanned": scanned,
        "decayed": decayed,
        "triggered_push_candidates": push_candidates,
    }
