"""搜索服务：同义词扩展 + 规则评分 + pgvector 向量检索 + RRF 融合 + Redis 缓存"""

import hashlib
import json
import logging
import math
import unicodedata

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import redis_client
from app.config import settings
from app.data.synonyms import SYNONYM_INDEX, expand_query as synonym_expand
from app.llm.embedding import encode as encode_embedding
from app.models import Memory

logger = logging.getLogger(__name__)

# RRF constant (standard k=60)
RRF_K = 60
# RRF fusion weights
RRF_WEIGHT = 0.7
STRENGTH_WEIGHT = 0.3
# Cache TTL
CACHE_TTL = 300  # 5 minutes
# Top-K candidates to pull from DB
CANDIDATE_LIMIT = 200
# Minimum cosine similarity to include in vector results
MIN_SIMILARITY = 0.6


# ─── C1: 查询扩展 ───


def expand_search_query(query: str) -> list[str]:
    """Expand query with synonyms. Returns deduplicated list with original first."""
    return synonym_expand(query)


# ─── C2: 模糊匹配增强 ───


def _strip_punctuation(text: str) -> str:
    """Remove punctuation for fuzzy matching."""
    return "".join(ch for ch in text if unicodedata.category(ch)[0] not in ("P", "S"))


def _score_memory(
    memory: Memory,
    query: str,
    expanded_terms: list[str],
) -> float:
    """Rule-based scoring with synonym expansion and fuzzy matching."""
    score = 0.0
    tags = memory.tags or {}
    content_lower = (memory.content or "").lower()
    content_stripped = _strip_punctuation(content_lower)
    expanded_lower = {_strip_punctuation(t.lower()) for t in expanded_terms}

    # Path 1: Entity anchoring (weight 0.5)
    entities = tags.get("entities", {})
    if isinstance(entities, dict):
        for _etype, evals in entities.items():
            if not isinstance(evals, list):
                continue
            matched = False
            for ev in evals:
                ev_lower = ev.lower()
                ev_stripped = _strip_punctuation(ev_lower)
                # Exact match with any expanded term
                if ev_lower in {t.lower() for t in expanded_terms}:
                    matched = True
                    break
                # Stripped match
                if ev_stripped and ev_stripped in expanded_lower:
                    matched = True
                    break
                # Synonym group match
                ev_key = SYNONYM_INDEX.get(ev_lower)
                if ev_key:
                    for t in expanded_terms:
                        if SYNONYM_INDEX.get(t.lower()) == ev_key:
                            matched = True
                            break
                if matched:
                    break
            if matched:
                score += 0.5
                break

    # Path 2: Keyword co-occurrence (weight 0.3)
    keywords = tags.get("keywords", [])
    if isinstance(keywords, list):
        kw_lower_set = {kw.lower() for kw in keywords}
        for term in expanded_terms:
            if term.lower() in kw_lower_set:
                score += 0.3
                break

    # Path 3: Substring fallback (weight 0.2)
    for term in expanded_terms:
        term_lower = term.lower()
        term_stripped = _strip_punctuation(term_lower)
        if term_lower in content_lower or (term_stripped and term_stripped in content_stripped):
            score += 0.2
            break

    return min(score, 1.0)


# ─── C3: pgvector 向量搜索 ───


async def _vector_search(
    db: AsyncSession,
    query_embedding: list[float],
    *,
    owner_id: str,
    scope: str | None = None,
    limit: int = 50,
) -> list[tuple[str, float]]:
    """Vector similarity search using pgvector cosine distance.

    Returns [(memory_id, similarity_score), ...] sorted by similarity desc.
    """
    # Use pgvector's cosine distance operator <=>
    # similarity = 1 - distance
    stmt = (
        select(
            Memory.id,
            (1 - Memory.embedding.cosine_distance(query_embedding)).label("similarity"),
        )
        .where(
            Memory.owner_id == owner_id,
            Memory.active == True,
            Memory.embedding != None,
        )
        .order_by(Memory.embedding.cosine_distance(query_embedding))
        .limit(limit)
    )
    if scope:
        stmt = stmt.where(Memory.scope == scope)

    result = await db.execute(stmt)
    return [(row.id, row.similarity) for row in result.all() if row.similarity >= MIN_SIMILARITY]


# ─── C4: RRF 融合 ───


def _reciprocal_rank_fusion(
    rule_results: list[tuple[str, float]],
    vector_results: list[tuple[str, float]],
    memories_by_id: dict[str, Memory],
) -> list[tuple[float, Memory]]:
    """Reciprocal Rank Fusion of rule and vector search results.

    final_score = rrf_score * RRF_WEIGHT + strength * STRENGTH_WEIGHT
    """
    rrf_scores: dict[str, float] = {}

    for rank, (mid, _score) in enumerate(rule_results, start=1):
        rrf_scores[mid] = rrf_scores.get(mid, 0.0) + 1.0 / (RRF_K + rank)

    for rank, (mid, _score) in enumerate(vector_results, start=1):
        rrf_scores[mid] = rrf_scores.get(mid, 0.0) + 1.0 / (RRF_K + rank)

    fused = []
    for mid, rrf in rrf_scores.items():
        m = memories_by_id.get(mid)
        if m is None:
            continue
        final = rrf * RRF_WEIGHT + m.strength * STRENGTH_WEIGHT
        fused.append((final, m))

    fused.sort(key=lambda x: x[0], reverse=True)
    return fused


# ─── C5: Redis 缓存 ───


def _cache_key(query: str, owner_id: str, scope: str | None) -> str:
    raw = f"{query}:{owner_id}:{scope or ''}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"search:{h}"


async def _get_cached_results(key: str) -> list[tuple[str, float]] | None:
    cached = await redis_client.get(key)
    if cached is None:
        return None
    try:
        return json.loads(cached)
    except (json.JSONDecodeError, TypeError):
        return None


async def _set_cached_results(key: str, results: list[tuple[str, float]]) -> None:
    await redis_client.set(key, json.dumps(results, ensure_ascii=False), ttl_seconds=CACHE_TTL)


async def invalidate_search_cache(owner_id: str | None = None) -> None:
    """Invalidate all search cache entries.

    Called after memory create/update/delete.
    """
    await redis_client.delete_pattern("search:*")


# ─── 主入口 ───


async def search_memories(
    db: AsyncSession,
    *,
    query: str,
    owner_id: str,
    scope: str | None = None,
    limit: int = 5,
) -> list[Memory]:
    """Hybrid search: synonym expansion + rule scoring + pgvector + RRF + cache."""
    # --- C5: Cache check ---
    cache_key = _cache_key(query, owner_id, scope)
    cached = await _get_cached_results(cache_key)
    if cached is not None:
        ids = [mid for mid, _score in cached[:limit]]
        result = await db.execute(select(Memory).where(Memory.id.in_(ids)))
        memories = {m.id: m for m in result.scalars().all()}
        return [memories[mid] for mid in ids if mid in memories]

    # --- C1: Query expansion ---
    expanded_terms = expand_search_query(query)

    # --- C2: Rule-based search ---
    base_query = select(Memory).where(
        Memory.owner_id == owner_id,
        Memory.active == True,
    )
    if scope:
        base_query = base_query.where(Memory.scope == scope)

    result = await db.execute(
        base_query.order_by(Memory.strength.desc()).limit(CANDIDATE_LIMIT)
    )
    all_memories = list(result.scalars().all())
    memories_by_id = {m.id: m for m in all_memories}

    rule_scored: list[tuple[str, float]] = []
    for m in all_memories:
        s = _score_memory(m, query, expanded_terms)
        if s > 0:
            rule_scored.append((m.id, s))
    rule_scored.sort(key=lambda x: x[1], reverse=True)

    # --- C3: Vector search via pgvector ---
    vector_scored: list[tuple[str, float]] = []
    query_embedding = await encode_embedding(query)
    if query_embedding is not None:
        try:
            vector_scored = await _vector_search(
                db, query_embedding, owner_id=owner_id, scope=scope, limit=50,
            )
            # Fetch vector-only candidates not already in memories_by_id
            for mid, _sim in vector_scored:
                if mid not in memories_by_id:
                    m = await db.get(Memory, mid)
                    if m:
                        memories_by_id[mid] = m
        except Exception as e:
            logger.warning("Vector search failed, falling back to rule-only: %s", e)
            vector_scored = []

    # --- C4: RRF Fusion ---
    fused = _reciprocal_rank_fusion(rule_scored, vector_scored, memories_by_id)

    # --- C5: Cache results ---
    cache_data = [(m.id, score) for score, m in fused]
    await _set_cached_results(cache_key, cache_data)

    return [m for _score, m in fused[:limit]]
