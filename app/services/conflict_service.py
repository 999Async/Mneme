"""冲突检测服务：三路检测（实体锚定→关键词共现→子串匹配）+ LLM 语义验证"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.client import chat_json
from app.llm.prompts import CONFLICT_DETECTION_PROMPT, CONFLICT_SCHEMA
from app.models import Memory

logger = logging.getLogger(__name__)

# 阈值配置
CANDIDATE_THRESHOLD = 0.5  # 初筛候选阈值
CONFLICT_THRESHOLD = 0.75  # 高置信冲突阈值（直接判定，跳过 LLM）


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
        candidates: 需要验证的候选列表（needs_llm_verify=True 的）

    Returns:
        {"conflicts": [...], "llm_available": bool, "llm_error": str | None}
        conflicts 中每条包含原始字段 + LLM 判定的 type/reason
    """
    if not candidates:
        return {"conflicts": [], "llm_available": True, "llm_error": None}

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
        # 降级：规则初筛结果全部保留
        return {
            "conflicts": candidates,
            "llm_available": False,
            "llm_error": result.get("error", "unknown"),
        }

    # 解析 LLM 结果
    llm_conflicts = result["data"].get("items", result["data"].get("conflicts", []))
    verified = []
    for item in llm_conflicts:
        idx = item.get("index", -1)
        if idx < 0 or idx >= len(candidates):
            continue
        if item.get("has_conflict") and item.get("type") != "supplement":
            candidate = candidates[idx]
            candidate["conflict_type"] = item.get("type", "update")
            candidate["llm_reason"] = item.get("reason", "")
            verified.append(candidate)

    return {"conflicts": verified, "llm_available": True, "llm_error": None}


async def detect_conflicts_with_llm(
    db: AsyncSession,
    *,
    content: str,
    tags: dict,
    owner_id: str,
    scope: str,
    exclude_id: str | None = None,
) -> dict:
    """双阈值 + LLM 增强的冲突检测

    Returns:
        {
            "conflicts": [...],
            "llm_available": bool,
            "llm_error": str | None,
        }
    """
    all_candidates = await detect_conflicts(
        db,
        content=content, tags=tags,
        owner_id=owner_id, scope=scope,
        exclude_id=exclude_id,
    )

    # 分离高置信和需验证的候选
    high_confidence = [c for c in all_candidates if not c.get("needs_llm_verify")]
    need_verify = [c for c in all_candidates if c.get("needs_llm_verify")]

    # 清理输出字段
    for c in high_confidence:
        c.pop("needs_llm_verify", None)

    if not need_verify:
        return {"conflicts": high_confidence, "llm_available": True, "llm_error": None}

    # LLM 验证低置信候选
    llm_result = await llm_verify_conflict(content, need_verify)

    # 清理 LLM 确认的候选字段
    for c in llm_result["conflicts"]:
        c.pop("needs_llm_verify", None)

    # 合并：高置信 + LLM 确认的
    merged = high_confidence + llm_result["conflicts"]
    merged.sort(key=lambda x: x["similarity_score"], reverse=True)

    return {
        "conflicts": merged,
        "llm_available": llm_result["llm_available"],
        "llm_error": llm_result["llm_error"],
    }
