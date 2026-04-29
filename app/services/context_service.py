"""心流恢复：上下文快照管理"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ContextSnapshot
from app.utils.datetime import ms_now


async def get_latest_snapshot(db: AsyncSession, user_id: str) -> ContextSnapshot | None:
    """获取用户最新的未过期快照"""
    now = ms_now()
    stmt = (
        select(ContextSnapshot)
        .where(ContextSnapshot.user_id == user_id, ContextSnapshot.expires_at > now)
        .order_by(ContextSnapshot.created_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()
