"""冲突检测服务：三路检测（实体锚定→关键词共现→子串匹配）+ LLM 语义验证"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.client import chat_json
from app.llm.prompts import CONFLICT_DETECTION_PROMPT, CONFLICT_SCHEMA
from app.models import Memory

logger = logging.getLogger(__name__)

# 阈值配置
CANDIDATE_THRESHOLD = 0.5  # 三路评分候选阈值（降级用）
CONFLICT_THRESHOLD = 0.75  # 三路评分高置信阈值（降级用）
EMBEDDING_THRESHOLD = 0.7  # embedding 相似度冲突阈值
EMBEDDING_HIGH_CONF = 0.85  # embedding 高置信阈值（跳过 LLM）
DUPLICATE_THRESHOLD = 0.95  # 去重阈值：内容完全相同或语义相似度 >= 此值视为重复


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


async def _detect_conflicts_by_embedding(
    db: AsyncSession,
    content: str,
    owner_id: str,
    scope: str,
    exclude_id: str | None = None,
) -> list[dict]:
    """用 embedding 余弦相似度做冲突初筛（主要路径）

    embedding 已存在于每条记忆中，零额外 API 调用。
    只需生成新内容的 embedding（1 次 API 调用）。
    """
    from app.llm.embedding import encode as encode_embedding

    query_vec = await encode_embedding(content)
    if not query_vec:
        return []

    stmt = (
        select(
            Memory,
            (1 - Memory.embedding.cosine_distance(query_vec)).label("similarity"),
        )
        .where(
            Memory.owner_id == owner_id,
            Memory.scope == scope,
            Memory.active == True,
            Memory.embedding != None,
        )
        .order_by(Memory.embedding.cosine_distance(query_vec))
        .limit(10)
    )
    if exclude_id:
        stmt = stmt.where(Memory.id != exclude_id)

    result = await db.execute(stmt)
    conflicts = []
    for memory, sim in result.all():
        if sim >= EMBEDDING_THRESHOLD:
            conflicts.append({
                "id": memory.id,
                "content": memory.content,
                "similarity_score": round(sim, 3),
                "conflict_reason": f"语义相似度 {sim:.2f}",
                "created_at": memory.created_at,
                "needs_llm_verify": sim < EMBEDDING_HIGH_CONF,
            })

    conflicts.sort(key=lambda x: x["similarity_score"], reverse=True)
    return conflicts


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
        if score < CANDIDATE_THRESHOLD:
            continue

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

        entry = {
            "id": candidate.id,
            "content": candidate.content,
            "similarity_score": round(score, 3),
            "conflict_reason": "; ".join(reasons) or "综合相似度匹配",
            "created_at": candidate.created_at,
            "needs_llm_verify": score < CONFLICT_THRESHOLD,
        }
        conflicts.append(entry)

    # 按相似度降序
    conflicts.sort(key=lambda x: x["similarity_score"], reverse=True)
    return conflicts


async def llm_verify_conflict(
    content: str,
    candidates: list[dict],
) -> dict:
    """LLM 二次验证候选冲突

    Args:
        content: 新记忆内容
        candidates: 需要验证的候选列表

    Returns:
        {
            "conflicts": [...],  # 需要 supersede 的（update）
            "duplicates": [...], # 需要跳过的重复（duplicate）
            "llm_available": bool,
            "llm_error": str | None
        }
    """
    if not candidates:
        return {"conflicts": [], "duplicates": [], "llm_available": True, "llm_error": None}

    # 格式化已有记忆文本
    memories_text = "\n".join(
        f"{i}. {c['content']}" for i, c in enumerate(candidates)
    )

    prompt = CONFLICT_DETECTION_PROMPT.format(
        content=content, memories_text=memories_text
    )

    messages = [
        {"role": "system", "content": "你是记忆冲突检测助手，只返回 JSON。"},
        {"role": "user", "content": prompt},
    ]

    result = await chat_json(messages, temperature=0.1, max_tokens=800, schema=CONFLICT_SCHEMA)

    if not result["ok"]:
        logger.warning("LLM conflict verify failed: %s", result.get("error"))
        return {
            "conflicts": candidates,
            "duplicates": [],
            "llm_available": False,
            "llm_error": result.get("error", "unknown"),
        }

    # 解析 LLM 结果
    llm_conflicts = result["data"].get("items", result["data"].get("conflicts", []))
    verified_conflicts = []
    verified_duplicates = []

    for item in llm_conflicts:
        idx = item.get("index", -1)
        if idx < 0 or idx >= len(candidates):
            continue

        conflict_type = item.get("type", "unrelated")

        if conflict_type == "duplicate":
            # 重复：跳过，不创建新记忆
            candidate = candidates[idx]
            candidate["llm_reason"] = item.get("reason", "")
            verified_duplicates.append(candidate)
        elif conflict_type == "update":
            # 更新：创建新版本，supersede 旧记忆
            candidate = candidates[idx]
            candidate["conflict_type"] = "update"
            candidate["llm_reason"] = item.get("reason", "")
            verified_conflicts.append(candidate)
        # supplement 和 unrelated 都不处理，创建独立记忆（默认行为）

    return {
        "conflicts": verified_conflicts,
        "duplicates": verified_duplicates,
        "llm_available": True,
        "llm_error": None,
    }


async def detect_conflicts_with_llm(
    db: AsyncSession,
    *,
    content: str,
    tags: dict,
    owner_id: str,
    scope: str,
    exclude_id: str | None = None,
) -> dict:
    """冲突检测：embedding 优先，降级到三路评分

    流程：
    1. embedding 余弦相似度初筛（主要路径，利用已有 embedding）
    2. 降级：embedding 不可用时回退到三路评分（实体+关键词+子串）
    3. 对低置信候选用 LLM 验证

    Returns:
        {"conflicts": [...], "llm_available": bool, "llm_error": str | None}
    """
    # 路径 1：embedding 相似度初筛
    try:
        embedding_conflicts = await _detect_conflicts_by_embedding(
            db, content, owner_id, scope, exclude_id,
        )
    except Exception as e:
        logger.warning("embedding 冲突检测失败，降级到三路评分: %s", e)
        embedding_conflicts = []

    if embedding_conflicts:
        high = [c for c in embedding_conflicts if not c.get("needs_llm_verify")]
        low = [c for c in embedding_conflicts if c.get("needs_llm_verify")]

        if low:
            llm_result = await llm_verify_conflict(content, low)
            verified = llm_result["conflicts"]
            for c in verified:
                c.pop("needs_llm_verify", None)
            llm_available = llm_result["llm_available"]
            llm_error = llm_result["llm_error"]
        else:
            verified = []
            llm_available = True
            llm_error = None

        for c in high:
            c.pop("needs_llm_verify", None)

        merged = high + verified
        merged.sort(key=lambda x: x["similarity_score"], reverse=True)
        return {"conflicts": merged, "llm_available": llm_available, "llm_error": llm_error}

    # 路径 2：降级到三路评分（原有逻辑）
    logger.info("embedding 无冲突候选，使用三路评分降级")
    all_candidates = await detect_conflicts(
        db,
        content=content, tags=tags,
        owner_id=owner_id, scope=scope,
        exclude_id=exclude_id,
    )

    high_confidence = [c for c in all_candidates if not c.get("needs_llm_verify")]
    need_verify = [c for c in all_candidates if c.get("needs_llm_verify")]

    for c in high_confidence:
        c.pop("needs_llm_verify", None)

    if not need_verify:
        return {"conflicts": high_confidence, "llm_available": True, "llm_error": None}

    llm_result = await llm_verify_conflict(content, need_verify)
    for c in llm_result["conflicts"]:
        c.pop("needs_llm_verify", None)

    merged = high_confidence + llm_result["conflicts"]
    merged.sort(key=lambda x: x["similarity_score"], reverse=True)

    return {
        "conflicts": merged,
        "llm_available": llm_result["llm_available"],
        "llm_error": llm_result["llm_error"],
    }


async def _find_exact_matches(
    db: AsyncSession,
    content: str,
    owner_id: str,
    scope: str,
    exclude_id: str | None = None,
) -> list[dict]:
    """查找内容完全相同的记忆

    Returns:
        [{"id", "content", "similarity_score", "duplicate_reason"}]
    """
    stmt = select(Memory).where(
        Memory.owner_id == owner_id,
        Memory.scope == scope,
        Memory.active == True,
        Memory.content == content,
    )
    if exclude_id:
        stmt = stmt.where(Memory.id != exclude_id)

    result = await db.execute(stmt.limit(1))
    memory = result.scalar_one_or_none()

    if memory:
        return [{
            "id": memory.id,
            "content": memory.content,
            "similarity_score": 1.0,
            "duplicate_reason": "内容完全相同",
        }]
    return []


async def _find_semantic_duplicates(
    db: AsyncSession,
    content: str,
    owner_id: str,
    scope: str,
    exclude_id: str | None = None,
) -> list[dict]:
    """查找语义高度相似的记忆（embedding >= 0.95）

    Returns:
        [{"id", "content", "similarity_score", "duplicate_reason"}]
    """
    from app.llm.embedding import encode as encode_embedding

    query_vec = await encode_embedding(content)
    if not query_vec:
        return []

    stmt = (
        select(Memory, (1 - Memory.embedding.cosine_distance(query_vec)).label("similarity"))
        .where(
            Memory.owner_id == owner_id,
            Memory.scope == scope,
            Memory.active == True,
            Memory.embedding != None,
        )
        .order_by(Memory.embedding.cosine_distance(query_vec))
        .limit(1)
    )
    if exclude_id:
        stmt = stmt.where(Memory.id != exclude_id)

    result = await db.execute(stmt)
    row = result.first()

    if row:
        memory, sim = row
        if sim >= DUPLICATE_THRESHOLD:
            return [{
                "id": memory.id,
                "content": memory.content,
                "similarity_score": round(sim, 3),
                "duplicate_reason": f"语义相似度 {sim:.2f}",
            }]
    return []


async def detect_duplicates(
    db: AsyncSession,
    *,
    content: str,
    owner_id: str,
    scope: str,
    exclude_id: str | None = None,
) -> list[dict]:
    """检测重复记忆

    优先检测内容完全相同的记忆，若无则检测语义高度相似的记忆。

    Returns:
        [{"id", "content", "similarity_score", "duplicate_reason"}]
    """
    exact = await _find_exact_matches(db, content, owner_id, scope, exclude_id)
    if exact:
        return exact
    return await _find_semantic_duplicates(db, content, owner_id, scope, exclude_id)
