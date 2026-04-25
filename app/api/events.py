from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.common import ok

router = APIRouter()


@router.post("/message")
async def handle_message(body: dict, db: AsyncSession = Depends(get_db)):
    """Plugin before_agent_start 调用入口"""
    # TODO: intent_service → extraction_service → conflict_service → memory_service
    return ok({"reply_text": None, "relevant_memories": []})


@router.post("/conversation-end")
async def handle_conversation_end(body: dict, db: AsyncSession = Depends(get_db)):
    """Plugin agent_end 调用入口：对话结束后自动抽取记忆"""
    # TODO: extraction_service 自动抽取
    return ok({"extracted": 0})
