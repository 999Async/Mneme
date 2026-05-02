"""消息缓冲服务 — Redis List 缓冲 + 双重触发（数量/时间）+ LLM 批量提取"""

import json
import logging
import time

from sqlalchemy.ext.asyncio import AsyncSession

from app.cache import redis_client
from app.config import settings
from app.llm.client import chat_json
from app.llm.prompts import MEMORY_EXTRACTION_PROMPT, MEMORY_EXTRACTION_SCHEMA
from app.rules.entity_rules import desensitize, extract_entities, extract_keywords
from app.services import extraction_service
from app.services.conflict_service import detect_conflicts_with_llm, detect_duplicates
from app.services.memory_service import create_memory, get_memory, supersede_memory

logger = logging.getLogger(__name__)


def _buffer_key(owner_id: str) -> str:
    return f"{settings.buffer_key_prefix}:{owner_id}"


def _ts_key(owner_id: str) -> str:
    return f"{settings.buffer_key_prefix}:ts:{owner_id}"


async def buffer_message(
    owner_id: str,
    content: str,
    *,
    role: str = "user",
    session_key: str | None = None,
    message_id: str | None = None,
) -> dict:
    """缓冲一条消息到 Redis List。

    Returns:
        {"buffered": bool, "buffer_count": int}
    """
    payload = json.dumps({
        "content": content,
        "role": role,
        "session_key": session_key,
        "message_id": message_id,
    }, ensure_ascii=False)

    ok = await redis_client.lpush(_buffer_key(owner_id), payload)
    if not ok:
        logger.warning("buffer_message: lpush failed for owner=%s", owner_id)
        return {"buffered": False, "buffer_count": 0}

    # 存时间戳
    await redis_client.set(_ts_key(owner_id), str(time.time()), ttl_seconds=86400)
    # 缓冲区 24h 自动清理
    await redis_client.expire(_buffer_key(owner_id), 86400)

    count = await redis_client.llen(_buffer_key(owner_id))
    return {"buffered": True, "buffer_count": count}


async def check_count_trigger(owner_id: str) -> bool:
    """检查缓冲区是否达到数量触发阈值。"""
    count = await redis_client.llen(_buffer_key(owner_id))
    return count >= settings.buffer_max_size


async def extract_from_buffer(
    owner_id: str,
    db: AsyncSession,
    *,
    force: bool = False,
) -> dict:
    """从缓冲区批量提取记忆。

    Args:
        force: conversation-end 时强制提取（忽略 buffer_min_size）

    Returns:
        {"extracted": int, "buffer_size": int, "llm_available": bool, "llm_error": str|None}
    """
    raw = await redis_client.lrange(_buffer_key(owner_id), 0, -1)
    buffer_size = len(raw)

    if not raw:
        return {"extracted": 0, "buffer_size": 0, "llm_available": True, "llm_error": None}

    # 解析消息
    messages = []
    for item in raw:
        try:
            msg = json.loads(item)
        except (json.JSONDecodeError, TypeError):
            logger.warning("extract_from_buffer: skip malformed JSON: %s", item[:100])
            continue
        content = msg.get("content", "")
        role = msg.get("role", "")
        if role == "assistant":
            continue
        if len(content) < 5:
            continue
        messages.append(msg)

    if not messages:
        # 全部被过滤，清理缓冲区
        await redis_client.delete(_buffer_key(owner_id))
        await redis_client.delete(_ts_key(owner_id))
        return {"extracted": 0, "buffer_size": buffer_size, "llm_available": True, "llm_error": None}

    # 非 force 模式检查最低数量
    if not force and len(messages) < settings.buffer_min_size:
        return {"extracted": 0, "buffer_size": buffer_size, "llm_available": True, "llm_error": None}

    # 拼接编号文本
    numbered = "\n".join(f"[{i+1}] {m['content']}" for i, m in enumerate(messages))

    # 尝试 LLM 批量提取
    prompt = MEMORY_EXTRACTION_PROMPT.format(messages=numbered)
    llm_messages = [
        {"role": "system", "content": "你是团队记忆提取助手，只返回 JSON。"},
        {"role": "user", "content": prompt},
    ]
    llm_result = await chat_json(llm_messages, temperature=0.2, max_tokens=2000, schema=MEMORY_EXTRACTION_SCHEMA)

    extracted_count = 0
    llm_available = True
    llm_error = None

    if llm_result["ok"]:
        # LLM 成功：遍历提取结果
        memories = llm_result["data"].get("memories", [])
        if not isinstance(memories, list):
            memories = []
        for mem in memories:
            confidence = mem.get("confidence", 0)
            if confidence < 0.7:
                continue

            content = mem.get("content", "")
            mem_type = mem.get("type", "fact")
            if not content:
                continue

            # 实体提取 + 关键词
            entities = extract_entities(content)
            keywords = extract_keywords(content)
            tags = {"keywords": keywords, "entities": entities}
            safe_content = desensitize(content)

            # 去重检测（优先于冲突检测）
            dupes = await detect_duplicates(
                db, content=safe_content, owner_id=owner_id, scope="group",
            )
            if dupes:
                continue  # 跳过重复记忆

            # 冲突检测
            conflict_result = await detect_conflicts_with_llm(
                db, content=safe_content, tags=tags,
                owner_id=owner_id, scope="group",
            )
            conflicts = conflict_result["conflicts"]
            llm_duplicates = conflict_result.get("duplicates", [])

            # LLM 判定的重复
            if llm_duplicates:
                continue  # 跳过重复记忆
            if not conflict_result.get("llm_available", True):
                llm_available = False
                llm_error = conflict_result.get("llm_error")

            # 创建记忆（附带来源信息）
            source_ids = [m.get("message_id") for m in messages if m.get("message_id")]
            context_snapshot = {
                "source_messages": [m.get("content", "")[:200] for m in messages[:5]],
                "source_count": len(messages),
                "source_message_ids": source_ids[:5],
                "session_key": messages[0].get("session_key") if messages else None,
            }
            memory = await create_memory(
                db, scope="group", owner_id=owner_id, type=mem_type,
                content=safe_content, tags=tags,
                confidence=confidence,
                context_snapshot=context_snapshot,
                source_chat_id=owner_id,
                source_message_id=source_ids[0] if source_ids else None,
            )

            # 处理冲突
            for conflict in conflicts:
                old = await get_memory(db, conflict["id"])
                if old:
                    await supersede_memory(db, old, memory.id)

            extracted_count += 1
    else:
        # LLM 失败：降级到规则提取
        llm_available = False
        llm_error = llm_result.get("error", "unknown")
        logger.warning("extract_from_buffer: LLM failed, degrading to rules for owner=%s", owner_id)

        for msg in messages:
            content = msg.get("content", "")
            ext = extraction_service.extract(content)
            if not ext:
                continue

            # 去重检测
            dupes = await detect_duplicates(
                db, content=ext.content, owner_id=owner_id, scope="group",
            )
            if dupes:
                continue

            conflict_result = await detect_conflicts_with_llm(
                db, content=ext.content, tags=ext.tags,
                owner_id=owner_id, scope="group",
            )
            conflicts = conflict_result["conflicts"]
            llm_duplicates = conflict_result.get("duplicates", [])

            if llm_duplicates:
                continue
            if not conflict_result.get("llm_available", True):
                llm_available = False
                llm_error = conflict_result.get("llm_error")

            memory = await create_memory(
                db, scope="group", owner_id=owner_id, type=ext.type,
                content=ext.content, tags=ext.tags,
                confidence=ext.confidence,
                context_snapshot={
                    "raw_content": msg.get("content", "")[:200],
                    "session_key": msg.get("session_key"),
                    "message_id": msg.get("message_id"),
                },
                source_chat_id=owner_id,
                source_message_id=msg.get("message_id"),
            )

            for conflict in conflicts:
                old = await get_memory(db, conflict["id"])
                if old:
                    await supersede_memory(db, old, memory.id)

            extracted_count += 1

    # 清理缓冲区
    await redis_client.delete(_buffer_key(owner_id))
    await redis_client.delete(_ts_key(owner_id))

    return {
        "extracted": extracted_count,
        "buffer_size": buffer_size,
        "llm_available": llm_available,
        "llm_error": llm_error,
    }


async def extract_all_buffers(db: AsyncSession) -> dict:
    """定时扫描所有缓冲区，提取静默超时的缓冲。

    Returns:
        {"scanned": int, "extracted": int, "buffers_processed": list[str], "errors": list[str]}
    """
    keys = await redis_client.scan_keys(f"{settings.buffer_key_prefix}:*")

    # 过滤掉时间戳 key，只保留缓冲区 key
    buffer_keys = [k for k in keys if ":ts:" not in k]

    scanned = len(buffer_keys)
    total_extracted = 0
    processed = []
    errors = []

    for bkey in buffer_keys:
        # 从 key 解析 owner_id: "mneme:buffer:{owner_id}"
        parts = bkey.split(":")
        if len(parts) < 3:
            continue
        owner_id = ":".join(parts[2:])

        # 读时间戳
        ts_str = await redis_client.get(_ts_key(owner_id))
        if ts_str:
            try:
                ts = float(ts_str)
                elapsed = time.time() - ts
                if elapsed < settings.buffer_ttl_seconds:
                    # 还在活跃收消息，跳过
                    continue
            except (ValueError, TypeError):
                pass

        # 超时或无时间戳 → 提取
        try:
            result = await extract_from_buffer(owner_id, db)
            total_extracted += result["extracted"]
            processed.append(owner_id)
        except Exception as e:
            logger.error("extract_all_buffers error for owner=%s: %s", owner_id, e)
            errors.append(f"{owner_id}: {e}")

    return {
        "scanned": scanned,
        "extracted": total_extracted,
        "buffers_processed": processed,
        "errors": errors,
    }
