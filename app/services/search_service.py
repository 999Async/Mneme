"""搜索服务：向量搜索 + Rerank + Redis 缓存"""

import hashlib
import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import redis_client
from app.llm.embedding import encode as encode_embedding
from app.llm.rerank import rerank
from app.models import Memory

logger = logging.getLogger(__name__)

# Cache TTL
CACHE_TTL = 300  # 5 minutes
# Minimum cosine similarity to include in results
MIN_SIMILARITY = 0.6  # 向量召回阈值
# Vector recall top-k for reranking
VECTOR_RECALL_LIMIT = 20  # 向量召回数量
# Whether to enable LLM rerank
ENABLE_RERANK = True


# ─── Redis 缓存 ───


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


# ─── 向量搜索 ───


async def _vector_search(
    db: AsyncSession,
    query_embedding: list[float],
    *,
    owner_id: str,
    scope: str | None = None,
    limit: int = 20,
) -> list[tuple[float, Memory]]:
    """Vector similarity search using pgvector cosine distance.

    Returns [(similarity_score, Memory), ...] sorted by similarity desc.
    """
    # Use pgvector's cosine distance operator <=>
    # similarity = 1 - distance
    stmt = (
        select(
            Memory,
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
    results = []
    for row in result.all():
        if row.similarity >= MIN_SIMILARITY:
            results.append((row.similarity, row.Memory))
            logger.info("Vector召回: similarity=%.3f, content=%s", row.similarity, row.Memory.content[:50])
    return results


# ─── 主入口 ───


async def search_memories(
    db: AsyncSession,
    *,
    query: str,
    owner_id: str,
    scope: str | None = None,
    limit: int = 5,
) -> list[Memory]:
    """Pure vector search with caching.

    Args:
        db: Database session
        query: Search query text
        owner_id: User ID to filter memories
        scope: Optional scope filter (e.g., "group", "personal")
        limit: Max results to return

    Returns:
        List of Memory objects sorted by semantic similarity
    """
    logger.info("搜索开始: query='%s', owner_id='%s', scope='%s'", query, owner_id, scope)

    # --- 先检查有多少记忆有 embedding ---
    count_stmt = select(Memory).where(
        Memory.owner_id == owner_id,
        Memory.active == True,
        Memory.embedding != None,
    )
    if scope:
        count_stmt = count_stmt.where(Memory.scope == scope)
    count_result = await db.execute(count_stmt)
    total_with_embedding = len(count_result.scalars().all())
    logger.info("用户 %s 共有 %d 条有 embedding 的记忆", owner_id, total_with_embedding)

    # --- Cache check ---
    cache_key = _cache_key(query, owner_id, scope)
    cached = await _get_cached_results(cache_key)
    if cached is not None and len(cached) > 0:  # 只使用非空缓存
        logger.info("命中缓存，返回 %d 条结果", len(cached))
        ids = [mid for mid, _score in cached[:limit]]
        result = await db.execute(
            select(Memory).where(Memory.id.in_(ids))
        )
        memories = {m.id: m for m in result.scalars().all()}
        # 按缓存顺序返回并打印详细信息
        results = []
        for mid, sim in cached[:limit]:
            if mid in memories:
                m = memories[mid]
                logger.info("缓存结果: similarity=%.3f, strength=%.3f, score=%.3f, content=%s",
                            sim, m.strength, sim * m.strength, m.content[:50])
                results.append(m)
        return results

    # --- Vector search ---
    query_embedding = await encode_embedding(query)
    if query_embedding is None:
        logger.warning("Failed to encode query for search")
        return []

    logger.info("查询 embedding 成功，维度: %d", len(query_embedding))

    try:
        # 向量召回更多候选（用于 rerank）
        vector_recall_limit = VECTOR_RECALL_LIMIT if ENABLE_RERANK else limit * 2
        scored_results = await _vector_search(
            db, query_embedding, owner_id=owner_id, scope=scope, limit=vector_recall_limit,
        )
    except Exception as e:
        logger.warning("Vector search failed: %s", e)
        return []

    logger.info("向量召回 %d 条", len(scored_results))

    if not scored_results:
        return []

    # --- Rerank ---
    if ENABLE_RERANK and len(scored_results) > 0:
        logger.info("开始 Rerank...")
        reranked = await rerank(query, scored_results)
        if reranked and reranked != scored_results:
            # 使用 rerank 结果，再按 strength 加权
            ranked = sorted(
                reranked,
                key=lambda x: x[0] * x[1].strength,
                reverse=True,
            )
            logger.info("Rerank 完成，返回 %d 条", min(len(ranked), limit))
        else:
            # Rerank 失败，使用向量排序
            ranked = sorted(
                scored_results,
                key=lambda x: x[0] * x[1].strength,
                reverse=True,
            )
    else:
        # 不使用 rerank，直接按向量相似度排序
        ranked = sorted(
            scored_results,
            key=lambda x: x[0] * x[1].strength,
            reverse=True,
        )

    # --- Cache results ---
    results = ranked[:limit]

    logger.info("最终返回 %d 条结果", len(results))

    # 打印最终返回结果的详细信息
    for sim, m in results:
        logger.info("返回结果: similarity=%.3f, strength=%.3f, score=%.3f, content=%s",
                    sim, m.strength, sim * m.strength, m.content[:50])

    # 只缓存非空结果
    if results:
        cache_data = [(m.id, sim) for sim, m in results]
        await _set_cached_results(cache_key, cache_data)

    return [m for _sim, m in results]
