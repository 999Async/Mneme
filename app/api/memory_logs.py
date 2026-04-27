from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.common import ok
from app.models import MemoryLog

router = APIRouter()


@router.post("")
async def create_memory_log(body: dict, db: AsyncSession = Depends(get_db)):
    """记录记忆操作日志"""
    log = MemoryLog(
        memory_id=body["memory_id"],
        action=body["action"],
        detail=body.get("detail", {}),
    )
    db.add(log)
    await db.commit()
    return ok({"id": log.id})
