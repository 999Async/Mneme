"""Redis 缓存封装 — 连接失败时静默降级"""

import json
import logging
from typing import Any

import redis.asyncio as aioredis

from app.config import settings

logger = logging.getLogger(__name__)

_client: aioredis.Redis | None = None
_available: bool = True


async def _get_client() -> aioredis.Redis | None:
    global _client, _available
    if not _available:
        return None
    if _client is not None:
        return _client
    try:
        _client = aioredis.from_url(settings.redis_url, decode_responses=True)
        await _client.ping()
        logger.info("Redis connected: %s", settings.redis_url)
        return _client
    except Exception as e:
        logger.warning("Redis unavailable, caching disabled: %s", e)
        _available = False
        return None


async def get(key: str) -> str | None:
    """获取缓存值"""
    client = await _get_client()
    if client is None:
        return None
    try:
        return await client.get(key)
    except Exception as e:
        logger.warning("Redis GET error for key=%s: %s", key, e)
        return None


async def set(key: str, value: Any, ttl_seconds: int = 300) -> bool:
    """设置缓存值，带 TTL"""
    client = await _get_client()
    if client is None:
        return False
    try:
        serialized = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        await client.set(key, serialized, ex=ttl_seconds)
        return True
    except Exception as e:
        logger.warning("Redis SET error for key=%s: %s", key, e)
        return False


async def delete(key: str) -> bool:
    """删除缓存"""
    client = await _get_client()
    if client is None:
        return False
    try:
        await client.delete(key)
        return True
    except Exception as e:
        logger.warning("Redis DELETE error for key=%s: %s", key, e)
        return False


async def delete_pattern(pattern: str) -> int:
    """按模式删除缓存（如 search:* ）"""
    client = await _get_client()
    if client is None:
        return 0
    try:
        keys = []
        async for key in client.scan_iter(match=pattern):
            keys.append(key)
        if keys:
            return await client.delete(*keys)
        return 0
    except Exception as e:
        logger.warning("Redis DELETE_PATTERN error for pattern=%s: %s", pattern, e)
        return 0


# --- 消息缓冲用（List 操作）---

async def lpush(key: str, value: str) -> bool:
    """从左侧推入列表"""
    client = await _get_client()
    if client is None:
        return False
    try:
        await client.lpush(key, value)
        return True
    except Exception as e:
        logger.warning("Redis LPUSH error for key=%s: %s", key, e)
        return False


async def lrange(key: str, start: int, end: int) -> list[str]:
    """获取列表指定范围"""
    client = await _get_client()
    if client is None:
        return []
    try:
        return await client.lrange(key, start, end)
    except Exception as e:
        logger.warning("Redis LRANGE error for key=%s: %s", key, e)
        return []


async def llen(key: str) -> int:
    """获取列表长度"""
    client = await _get_client()
    if client is None:
        return 0
    try:
        return await client.llen(key)
    except Exception as e:
        logger.warning("Redis LLEN error for key=%s: %s", key, e)
        return 0


async def ltrim(key: str, start: int, end: int) -> bool:
    """裁剪列表，只保留指定范围"""
    client = await _get_client()
    if client is None:
        return False
    try:
        await client.ltrim(key, start, end)
        return True
    except Exception as e:
        logger.warning("Redis LTRIM error for key=%s: %s", key, e)
        return False


async def expire(key: str, ttl_seconds: int) -> bool:
    """设置 key 过期时间"""
    client = await _get_client()
    if client is None:
        return False
    try:
        await client.expire(key, ttl_seconds)
        return True
    except Exception as e:
        logger.warning("Redis EXPIRE error for key=%s: %s", key, e)
        return False


async def scan_keys(pattern: str) -> list[str]:
    """扫描匹配模式的所有 key"""
    client = await _get_client()
    if client is None:
        return []
    try:
        keys = []
        async for key in client.scan_iter(match=pattern):
            keys.append(key)
        return keys
    except Exception as e:
        logger.warning("Redis SCAN error for pattern=%s: %s", pattern, e)
        return []


async def close():
    """关闭连接"""
    global _client
    if _client is not None:
        await _client.close()
        _client = None
