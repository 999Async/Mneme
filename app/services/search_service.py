"""搜索服务：三路召回（实体锚定→关键词共现→子串匹配）"""

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Memory


async def search_memories(
    db: AsyncSession,
    *,
    query: str,
    owner_id: str,
    scope: str | None = None,
    limit: int = 5,
) -> list[Memory]:
    """三路召回搜索（SQLite/PostgreSQL 兼容）

    路1 实体锚定：tags["entities"] 匹配（Python 侧过滤）
    路2 关键词共现：tags["keywords"] 交集（Python 侧过滤）
    路3 子串兜底：content ILIKE
    """
    base_query = select(Memory).where(
        Memory.owner_id == owner_id,
        Memory.active == True,
    )
    if scope:
        base_query = base_query.where(Memory.scope == scope)

    # 先拉取所有候选（content 子串匹配 + 全量），在 Python 侧做三路评分
    # content 子串匹配
    content_query = base_query.where(Memory.content.ilike(f"%{query}%"))
    result = await db.execute(content_query.order_by(Memory.strength.desc()).limit(50))
    content_hits = list(result.scalars().all())

    # 同时拉取该 owner 所有活跃记忆做关键词/实体匹配
    all_result = await db.execute(
        base_query.order_by(Memory.strength.desc()).limit(200)
    )
    all_memories = list(all_result.scalars().all())

    # Python 侧三路评分
    scored: dict[str, tuple[float, Memory]] = {}

    for m in all_memories:
        score = 0.0
        tags = m.tags or {}

        # 路1 实体锚定（权重 0.5）
        entities = tags.get("entities", {})
        if isinstance(entities, dict):
            for _etype, evals in entities.items():
                if isinstance(evals, list) and query in evals:
                    score += 0.5
                    break

        # 路2 关键词共现（权重 0.3）
        keywords = tags.get("keywords", [])
        if isinstance(keywords, list) and query in keywords:
            score += 0.3

        # 路3 子串匹配（权重 0.2）
        if query.lower() in (m.content or "").lower():
            score += 0.2

        if score > 0:
            scored[m.id] = (score, m)

    # 合并：按评分降序
    ranked = sorted(scored.values(), key=lambda x: x[0], reverse=True)
    return [m for _, m in ranked[:limit]]
