"""统一入口：Plugin before_agent_start / agent_end → Python 业务逻辑"""

import asyncio

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.common import ok
from app.schemas.event import ConversationEndEvent, IncomingEvent
from app.services import intent_service, extraction_service
from app.utils.datetime import ms_to_strftime
from app.services.conflict_service import detect_conflicts_with_llm
from app.services.memory_service import create_memory, supersede_memory
from app.services.context_service import save_snapshot
from app.services import feishu_base_service

router = APIRouter()

import logging
logger = logging.getLogger(__name__)

@router.post("/message")
async def handle_message(body: IncomingEvent, db: AsyncSession = Depends(get_db)):
    """Plugin before_agent_start 调用入口

    返回格式：{ok, data: {action, reply_text, relevant_memories, push_card}}
    """
    # 幂等去重：同一 event_id 5 分钟内不重复处理
    if body.event_id:
        from app.cache import redis_client
        dedupe_key = f"event:{body.event_id}"
        seen = await redis_client.get(dedupe_key)
        if seen:
            return ok({"action": "none", "reply_text": None, "relevant_memories": []})
        await redis_client.set(dedupe_key, "1", ttl_seconds=300)

    intent, params = await intent_service.identify_with_llm_fallback(body.content, body.is_mentioned)

    # --- STORE ---
    if intent == intent_service.Intent.STORE:
        scope = params.get("scope", "personal")
        extracted = extraction_service.extract_from_command(body.content, scope)
        # 解析 owner_id 和 chat_id
        owner_id, chat_id = _parse_owner_id(body.sender_id, body.session_key)

        # 预计算 embedding（冲突检测和存储都需要，避免重复 API 调用）
        from app.llm.embedding import encode
        pre_embedding = await encode(extracted.content)

        # 去重检测（优先于冲突检测，使用 0.95 高阈值）
        from app.services.conflict_service import detect_duplicates
        exact_duplicates = await detect_duplicates(
            db, content=extracted.content, owner_id=owner_id, scope=scope,
        )
        if exact_duplicates:
            dup_ids = ", ".join([d["id"] for d in exact_duplicates])  # 只显示前 8 位
            return ok({
                "action": "reply",
                "reply_text": f"这条记忆已经存在了哦~",
                "relevant_memories": [],
                "push_card": None,
            })

        # 冲突检测（embedding 优先，降级到三路评分）
        result = await detect_conflicts_with_llm(
            db, content=extracted.content, tags=extracted.tags,
            owner_id=owner_id, scope=scope,
        )
        conflicts = result["conflicts"]
        llm_duplicates = result.get("duplicates", [])

        # LLM 判定的重复
        if llm_duplicates:
            dup_ids = ", ".join([d["id"][:8] for d in llm_duplicates])
            dup_reasons = "; ".join([d.get("llm_reason", "语义相同") for d in llm_duplicates])
            return ok({
                "action": "reply",
                "reply_text": f"这条记忆与已有记忆语义相同，不需要重复记录哦~ 记忆ID: {dup_ids}",
                "relevant_memories": [],
                "push_card": None,
            })

        # 创建新记忆（复用预计算的 embedding）
        context_snapshot = {
            "raw_content": body.content,
            "session_key": body.session_key,
            "message_id": body.message_id,
        }
        memory = await create_memory(
            db, scope=scope, owner_id=owner_id, type=extracted.type,
            content=extracted.content, tags=extracted.tags,
            confidence=extracted.confidence,
            context_snapshot=context_snapshot,
            source_chat_id=chat_id,  # 使用会话 ID，而不是 owner_id
            source_message_id=body.message_id,
            embedding=pre_embedding,
            attachments=extracted.attachments,
        )

        # 保存上下文快照（best-effort，用于心流恢复）
        try:
            await save_snapshot(
                db, user_id=owner_id, chat_id=chat_id,  # 使用会话 ID
                snapshot={"last_content": extracted.content, "session_key": body.session_key},
            )
        except Exception:
            pass

        # 如果有冲突，覆写旧记忆
        for conflict in conflicts:
            from app.services.memory_service import get_memory
            old = await get_memory(db, conflict["id"])
            if old:
                await supersede_memory(db, old, memory.id)
                memory.parent_id = old.id
                memory.version = old.version + 1
                # 注意：memory 已经在 create_memory 中被 add 和 commit，这里不需要再次 add
                await db.commit()

        conflict_info = ""
        if conflicts:
            conflict_info = f"（已更新 {len(conflicts)} 条旧记忆）"
        if not result.get("llm_available", True):
            conflict_info += "（⚠️ AI 服务暂时不可用，冲突检测可能不够准确）"

        # 选择表情：有冲突更新用 THUMBSUP，普通存储用 DONE
        emoji = "Get" if conflicts else "DONE"

        # 多维表格同步（完全后台，不阻塞响应）
        asyncio.create_task(_sync_base_background(
            chat_id, memory, [c["id"] for c in conflicts],  # 使用会话 ID
            session_key=body.session_key, owner_id=owner_id,
        ))

        return ok({
            "action": "react",
            "reaction_emoji": emoji,
            "reply_text": None,
            "relevant_memories": [],
            "push_card": None,
        })

    # --- OVERWRITE ---
    if intent == intent_service.Intent.OVERWRITE:
        scope = params.get("scope", "personal")
        extracted = extraction_service.extract_from_command(body.content, scope)
        owner_id, chat_id = _parse_owner_id(body.sender_id, body.session_key)

        # 搜索旧记忆
        from app.services.search_service import search_memories
        import re
        clean = re.sub(r"@\S+\s*", "", body.content)
        for kw in ["覆盖", "改一下", "更新记忆", "删除记忆", "忘掉"]:
            clean = clean.replace(kw, "")
        clean = clean.strip()
        old_memories = await search_memories(db, query=clean, owner_id=owner_id, limit=3) if clean else []

        if not old_memories:
            return ok({
                "action": "reply",
                "reply_text": "未找到可覆盖的记忆。请先告诉我具体要更新哪条记忆。",
                "relevant_memories": [],
            })

        # 覆写第一条匹配的记忆
        old = old_memories[0]
        new_memory = await create_memory(
            db, scope=scope, owner_id=owner_id, type=extracted.type or old.type,
            content=extracted.content, tags=extracted.tags,
            confidence=extracted.confidence,
            context_snapshot={"raw_content": body.content, "session_key": body.session_key},
            source_chat_id=chat_id,  # 使用会话 ID
            source_message_id=body.message_id,
            parent_id=old.id,
            attachments=extracted.attachments,
        )
        await supersede_memory(db, old, new_memory.id)
        # 注意：new_memory 已经在 create_memory 中被 add 和 commit，这里不需要再次 add
        await db.commit()

        # 多维表格同步（后台）
        asyncio.create_task(_sync_base_background(
            chat_id, new_memory, [old.id],  # 使用会话 ID
            session_key=body.session_key, owner_id=owner_id,
        ))

        return ok({
            "action": "react",
            "reaction_emoji": "Get",
            "reply_text": f"已更新记忆（v{old.version} → v{old.version + 1}）",
            "relevant_memories": [],
        })

    # --- QUERY ---
    if intent == intent_service.Intent.QUERY:
        owner_id, _ = _parse_owner_id(body.sender_id, body.session_key)
        # 清理搜索词：去掉 @提及 + 指令关键词
        import re
        clean = re.sub(r"@\S+\s*", "", body.content)
        for kw in ["查询", "搜索", "搜一下", "查一下", "有没有"]:
            clean = clean.replace(kw, "")
        clean = clean.strip()
        from app.services.search_service import search_memories
        results = await search_memories(db, query=clean, owner_id=owner_id, limit=5)

        if results:
            # 返回结构化记忆数据，bridge 构建卡片
            mem_list = []
            for r in results:
                mem_list.append({
                    "id": r.id,
                    "type": r.type,
                    "content": r.content,
                    "strength": round(r.strength, 2),
                    "version": r.version,
                })
            return ok({
                "action": "reply",
                "reply_text": f"找到 {len(results)} 条相关记忆",
                "relevant_memories": mem_list,
            })
        return ok({
            "action": "reply",
            "reply_text": "未找到相关记忆，我可以帮你记住吗？",
            "relevant_memories": [],
        })

    # --- FLOW_RECOVER ---
    if intent == intent_service.Intent.FLOW_RECOVER:
        from app.services.context_service import get_latest_snapshot
        owner_id, _ = _parse_owner_id(body.sender_id, body.session_key)
        snapshot = await get_latest_snapshot(db, user_id=owner_id)
        if snapshot:
            return ok({
                "action": "reply",
                "reply_text": f"🔄 心流恢复：\n你刚才在讨论：{snapshot.snapshot}",
                "relevant_memories": [],
            })
        return ok({
            "action": "reply",
            "reply_text": "没有找到最近的上下文快照。",
            "relevant_memories": [],
        })

    # --- LIST_MY ---
    if intent == intent_service.Intent.LIST_MY:
        owner_id, _ = _parse_owner_id(body.sender_id, body.session_key)
        from app.services.memory_service import list_memories
        items, total = await list_memories(db, owner_id=owner_id, active_only=True, page_size=10)
        if items:
            text = "\n".join(f"- [{m.type}] {m.content} ({ms_to_strftime(m.created_at, '%m/%d')})" for m in items)
            return ok({
                "action": "reply",
                "reply_text": f"你的记忆 ({total} 条)：\n{text}",
                "relevant_memories": [],
            })
        return ok({
            "action": "reply",
            "reply_text": "你还没有任何记忆。",
            "relevant_memories": [],
        })

    # --- AUTO_EXTRACT (非 @Mneme 消息) → 缓冲 ---
    if intent == intent_service.Intent.AUTO_EXTRACT:
        owner_id, chat_id = _parse_owner_id(body.sender_id, body.session_key)
        from app.services.message_buffer_service import buffer_message, check_count_trigger, extract_from_buffer
        result = await buffer_message(owner_id, body.content, role="user", session_key=body.session_key, message_id=body.message_id)

        # 保存上下文快照（best-effort，不阻塞主流程）
        if result.get("buffered") and len(body.content) >= 5:
            try:
                await save_snapshot(
                    db, user_id=owner_id, chat_id=chat_id,  # 使用会话 ID
                    snapshot={"last_content": body.content, "session_key": body.session_key},
                )
                await db.commit()
            except Exception:
                pass  # 快照写入失败不影响消息处理

        if not result["buffered"]:
            return ok({"action": "none", "reply_text": None, "relevant_memories": []})

        if await check_count_trigger(owner_id):
            await extract_from_buffer(owner_id, db)

        return ok({"action": "none", "reply_text": None, "relevant_memories": []})

    # --- PASS ---
    return ok({"action": "none", "reply_text": None, "relevant_memories": []})


@router.post("/conversation-end")
async def handle_conversation_end(body: ConversationEndEvent, db: AsyncSession = Depends(get_db)):
    """Plugin agent_end：对话结束后缓冲消息并批量提取"""
    if not body.messages:
        return ok({"extracted": 0})

    # conversation-end 没有 sender_id，所以 owner_id = chat_id（会话 ID）
    owner_id, _ = _parse_owner_id(None, body.session_key)
    from app.services.message_buffer_service import buffer_message, extract_from_buffer

    for msg in body.messages:
        content = msg.get("content", "")
        if not content or len(content) < 5:
            continue
        role = msg.get("role", "")
        if role == "assistant":
            continue
        await buffer_message(owner_id, content, role=role, session_key=body.session_key)

    result = await extract_from_buffer(owner_id, db, force=True)
    return ok({"extracted": result["extracted"]})


def _parse_owner_id(sender_id: str | None, session_key: str | None) -> tuple[str, str]:
    """获取 owner_id 和 chat_id

    Returns:
        (owner_id, chat_id)
        - owner_id: 用户 ID（ou_xxx）用于隔离用户的记忆
        - chat_id: 会话 ID（oc_xxx）用于同步到正确的多维表格

    sender_id: "ou_xxx"（用户 open_id）
    session_key: "feishu:chat:oc_xxx" → owner_id=sender_id, chat_id="oc_xxx"
                  "feishu:p2p:oc_xxx" → owner_id=sender_id, chat_id="oc_xxx"
    """
    # 从 session_key 解析 chat_id（会话 ID）
    chat_id = "unknown"
    if session_key:
        parts = session_key.split(":")
        if len(parts) >= 3:
            chat_id = parts[2]
            logger.info("session_key 解析: %s → chat_id=%s", session_key, chat_id)

    # owner_id 优先使用 sender_id（真实用户 ID）
    if sender_id:
        logger.info("使用 sender_id 作为 owner_id: %s", sender_id)
        return sender_id, chat_id

    # 降级：没有 sender_id 时，owner_id 使用 chat_id（兼容旧数据）
    logger.warning("未提供 sender_id，owner_id 使用 chat_id: %s", chat_id)
    return chat_id, chat_id


async def _sync_base_background(chat_id: str, memory, conflict_ids: list[str], session_key: str | None = None, owner_id: str | None = None):
    """后台执行多维表格同步（ensure + upsert），不阻塞主响应"""
    try:
        from app.services.memory_service import get_memory as _get_mem
        from app.db.session import async_session

        base_config = await feishu_base_service.ensure_base(
            chat_id, session_key=session_key, owner_id=owner_id
        )
        if not base_config:
            return

        async with async_session() as db:
            # 同步新记忆
            ok = await feishu_base_service.upsert_record(memory)
            # upsert 失败时清除脏缓存并重试一次
            if not ok:
                import logging
                logging.getLogger(__name__).warning("upsert 首次失败，清除缓存重试")
                await feishu_base_service.invalidate_cache(chat_id)
                base_config = await feishu_base_service.ensure_base(chat_id)
                if base_config:
                    ok = await feishu_base_service.upsert_record(memory)
            # 同步被覆写的旧记忆
            for cid in conflict_ids:
                old_mem = await _get_mem(db, cid)
                if old_mem:
                    await feishu_base_service.upsert_record(old_mem)
            # 首次创建时通过 bridge 发通知
            if base_config and base_config.get("is_new") and base_config.get("url"):
                from app.services.feishu_push_service import feishu_push
                await feishu_push.send_text(chat_id, f"📊 记忆管理面板已创建：{base_config['url']}")
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("base 后台同步失败: %s", e)
