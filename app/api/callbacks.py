"""飞书卡片按钮回调处理"""

import json
import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.schemas.common import ok
from app.services import feishu_base_service
from app.services.memory_service import review_memory, get_memory, get_history

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/card-action")
async def handle_card_action(request: Request, db: AsyncSession = Depends(get_db)):
    """处理飞书卡片按钮回调

    飞书回调格式:
    {
        "token": "verification_token",
        "action": {"value": {"action": "review", "memory_id": "xxx", "chat_id": "xxx"}},
        "operator": {"open_id": "ou_xxx"}
    }
    """
    body = await request.json()

    # 验证 token
    token = body.get("token", "")
    if settings.feishu_callback_token and token != settings.feishu_callback_token:
        logger.warning("卡片回调 token 不匹配")
        return {"code": 1, "msg": "invalid token"}

    # 解析 action value
    action_value = body.get("action", {}).get("value", {})
    action_type = action_value.get("action", "")
    memory_id = action_value.get("memory_id", "")
    chat_id = action_value.get("chat_id", "")
    user_id = body.get("operator", {}).get("open_id", "unknown")

    if not action_type or not memory_id:
        return {"code": 0, "msg": "ok"}

    logger.info("卡片回调: action=%s memory_id=%s user=%s", action_type, memory_id[:8], user_id[:8])

    # 执行对应操作（后台异步，不阻塞回调响应）
    import asyncio

    if action_type == "history":
        # 版本历史：查询后发卡片到群聊
        asyncio.create_task(_handle_history(db, memory_id, chat_id))
    else:
        # review / done / dismiss
        asyncio.create_task(_handle_review(db, memory_id, chat_id, action_type, user_id))

    return {"code": 0, "msg": "ok"}


async def _handle_review(db: AsyncSession, memory_id: str, chat_id: str, action: str, user_id: str):
    """处理 review/done/dismiss 按钮"""
    try:
        memory = await review_memory(db, memory_id, action, user_id)
        if not memory:
            logger.warning("review_memory 返回 None: %s", memory_id[:8])
            return

        # 同步到多维表格
        await feishu_base_service.upsert_record(memory)

        # 发送确认消息
        confirm_text = {
            "review": "✅ 记忆已复习，强度已恢复",
            "done": "🙋 已标记处理",
            "dismiss": "❌ 已不再提醒此记忆",
        }.get(action, "已处理")

        from app.services.feishu_push_service import feishu_push
        await feishu_push.send_text(chat_id, confirm_text)

        logger.info("卡片回调处理完成: action=%s memory_id=%s", action, memory_id[:8])
    except Exception as e:
        logger.error("卡片回调处理异常: %s", e)


async def _handle_history(db: AsyncSession, memory_id: str, chat_id: str):
    """处理版本历史按钮"""
    try:
        history = await get_history(db, memory_id)
        if not history:
            from app.services.feishu_push_service import feishu_push
            await feishu_push.send_text(chat_id, "该记忆没有版本历史。")
            return

        # 构建版本历史文本
        lines = []
        for i, m in enumerate(reversed(history)):
            version = m.version or (i + 1)
            status = "✅ 当前" if m.active else "⏳ 已覆写"
            from app.utils.datetime import ms_to_strftime
            created = ms_to_strftime(m.created_at, "%m月%d日 %H:%M") if m.created_at else ""
            lines.append(f"**v{version}** {status} | {created}\n{m.content}")

        text = "📜 版本历史：\n\n" + "\n\n---\n\n".join(lines)

        from app.services.feishu_push_service import feishu_push
        await feishu_push.send_text(chat_id, text)

        logger.info("版本历史发送完成: memory_id=%s versions=%d", memory_id[:8], len(history))
    except Exception as e:
        logger.error("版本历史处理异常: %s", e)
