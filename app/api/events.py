"""统一入口：Plugin before_agent_start / agent_end → Python 业务逻辑"""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.common import ok
from app.schemas.event import ConversationEndEvent, IncomingEvent
from app.services import intent_service, extraction_service
from app.utils.datetime import ms_to_strftime
from app.services.conflict_service import detect_conflicts_with_llm
from app.services.memory_service import create_memory, supersede_memory

router = APIRouter()


@router.post("/message")
async def handle_message(body: IncomingEvent, db: AsyncSession = Depends(get_db)):
    """Plugin before_agent_start 调用入口

    返回格式：{ok, data: {action, reply_text, relevant_memories, push_card}}
    """
    intent, params = intent_service.identify(body.content, body.is_mentioned)

    # --- STORE ---
    if intent == intent_service.Intent.STORE:
        scope = params.get("scope", "personal")
        extracted = extraction_service.extract_from_command(body.content, scope)
        # 解析 owner_id from session_key (格式: "feishu:chat:oc_xxx" or "feishu:p2p:ou_xxx")
        owner_id = _parse_owner_id(body.session_key)

        # 冲突检测（LLM 增强）
        result = await detect_conflicts_with_llm(
            db, content=extracted.content, tags=extracted.tags,
            owner_id=owner_id, scope=scope,
        )
        conflicts = result["conflicts"]

        # 创建新记忆
        memory = await create_memory(
            db, scope=scope, owner_id=owner_id, type=extracted.type,
            content=extracted.content, tags=extracted.tags,
            confidence=extracted.confidence,
            context_snapshot=extracted.context_snapshot,
        )

        # 如果有冲突，覆写旧记忆
        for conflict in conflicts:
            from app.services.memory_service import get_memory
            old = await get_memory(db, conflict["id"])
            if old:
                await supersede_memory(db, old, memory.id)
                memory.parent_id = old.id
                memory.version = old.version + 1
                db.add(memory)
                await db.commit()

        conflict_info = ""
        if conflicts:
            conflict_info = f"（已更新 {len(conflicts)} 条旧记忆）"
        if not result.get("llm_available", True):
            conflict_info += "（⚠️ AI 服务暂时不可用，冲突检测可能不够准确）"

        # 选择表情：有冲突更新用 THUMBSUP，普通存储用 DONE
        emoji = "THUMBSUP" if conflicts else "DONE"

        return ok({
            "action": "react",
            "reaction_emoji": emoji,
            "reply_text": None,
            "relevant_memories": [],
            "push_card": None,
        })

    # --- QUERY ---
    if intent == intent_service.Intent.QUERY:
        owner_id = _parse_owner_id(body.session_key)
        # 清理搜索词：去掉 @提及 + 指令关键词
        import re
        clean = re.sub(r"@\S+\s*", "", body.content)
        for kw in ["查询", "搜索", "搜一下", "查一下", "有没有"]:
            clean = clean.replace(kw, "")
        clean = clean.strip()
        from app.services.search_service import search_memories
        results = await search_memories(db, query=clean, owner_id=owner_id, limit=5)

        if results:
            text = "\n".join(f"- [{r.type}] {r.content}" for r in results)
            return ok({
                "action": "reply",
                "reply_text": f"找到 {len(results)} 条相关记忆：\n{text}",
                "relevant_memories": [],
            })
        return ok({
            "action": "reply",
            "reply_text": "未找到相关记忆，我可以帮你记住吗？",
            "relevant_memories": [],
        })

    # --- FLOW_RECOVER ---
    if intent == intent_service.Intent.FLOW_RECOVER:
        from app.services.context_service import get_latest_snapshot
        owner_id = _parse_owner_id(body.session_key)
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
        owner_id = _parse_owner_id(body.session_key)
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
        owner_id = _parse_owner_id(body.session_key)
        from app.services.message_buffer_service import buffer_message, check_count_trigger, extract_from_buffer
        result = await buffer_message(owner_id, body.content, role="user", session_key=body.session_key)

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

    owner_id = _parse_owner_id(body.session_key)
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


def _parse_owner_id(session_key: str | None) -> str:
    """从 session_key 解析 owner_id

    "feishu:chat:oc_xxx" → "oc_xxx"
    "feishu:p2p:ou_xxx" → "ou_xxx"
    """
    if not session_key:
        return "unknown"
    parts = session_key.split(":")
    if len(parts) >= 3:
        return parts[2]
    return session_key
