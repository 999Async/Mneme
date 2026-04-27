"""冲突检测服务：三路检测（实体锚定→关键词共现→子串匹配）"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Memory

# 冲突阈值（PRD BR-004）
CONFLICT_THRESHOLD = 0.75


def _score_entity_overlap(tags_a: dict, tags_b: dict) -> float:
    """实体锚定评分（权重 0.5）"""
    entities_a = tags_a.get("entities", {})
    entities_b = tags_b.get("entities", {})
    if not entities_a or not entities_b:
        return 0.0

    overlap = 0
    total = 0
    for etype in set(list(entities_a.keys()) + list(entities_b.keys())):
        vals_a = set(entities_a.get(etype, []) if isinstance(entities_a.get(etype), list) else [])
        vals_b = set(entities_b.get(etype, []) if isinstance(entities_b.get(etype), list) else [])
        if vals_a and vals_b:
            total += 1
            if vals_a & vals_b:
                overlap += 1

    return (overlap / total * 0.5) if total > 0 else 0.0


def _score_keyword_overlap(tags_a: dict, tags_b: dict) -> float:
    """关键词共现评分（权重 0.3）"""
    kws_a = set(tags_a.get("keywords", []))
    kws_b = set(tags_b.get("keywords", []))
    if not kws_a or not kws_b:
        return 0.0

    intersection = kws_a & kws_b
    if len(intersection) < 2:
        return 0.0

    jaccard = len(intersection) / len(kws_a | kws_b)
    return jaccard * 0.3


def _score_substring_match(content_a: str, content_b: str) -> float:
    """子串匹配评分（权重 0.2）"""
    if not content_a or not content_b:
        return 0.0
    if content_a in content_b or content_b in content_a:
        shorter = min(len(content_a), len(content_b))
        longer = max(len(content_a), len(content_b))
        return (shorter / longer) * 0.2
    return 0.0


def compute_similarity(content: str, tags: dict, other: Memory) -> float:
    """计算综合相似度"""
    s1 = _score_entity_overlap(tags, other.tags or {})
    s2 = _score_keyword_overlap(tags, other.tags or {})
    s3 = _score_substring_match(content, other.content)
    return s1 + s2 + s3


async def detect_conflicts(
    db: AsyncSession,
    *,
    content: str,
    tags: dict,
    owner_id: str,
    scope: str,
    exclude_id: str | None = None,
) -> list[dict]:
    """三路冲突检测

    Returns:
        [{"id", "content", "similarity_score", "conflict_reason", "created_at"}]
    """
    # 查询同一 owner + scope 的活跃记忆
    query = select(Memory).where(
        Memory.owner_id == owner_id,
        Memory.scope == scope,
        Memory.active == True,
    )
    if exclude_id:
        query = query.where(Memory.id != exclude_id)

    result = await db.execute(query)
    candidates = list(result.scalars().all())

    conflicts = []
    for candidate in candidates:
        score = compute_similarity(content, tags, candidate)
        if score >= CONFLICT_THRESHOLD:
            # 生成冲突原因
            entity_overlap = _score_entity_overlap(tags, candidate.tags or {})
            kw_overlap = _score_keyword_overlap(tags, candidate.tags or {})
            reasons = []
            if entity_overlap > 0:
                reasons.append(f"实体重叠 (score={entity_overlap:.2f})")
            if kw_overlap > 0:
                reasons.append(f"关键词共现 (score={kw_overlap:.2f})")
            if _score_substring_match(content, candidate.content) > 0:
                reasons.append("内容子串匹配")

            conflicts.append({
                "id": candidate.id,
                "content": candidate.content,
                "similarity_score": round(score, 3),
                "conflict_reason": "; ".join(reasons) or "综合相似度匹配",
                "created_at": candidate.created_at.isoformat() if candidate.created_at else "",
            })

    # 按相似度降序
    conflicts.sort(key=lambda x: x["similarity_score"], reverse=True)
    return conflicts
