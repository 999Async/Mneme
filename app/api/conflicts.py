from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.common import ok
from app.schemas.conflict import ConflictDetectReq
from app.services.conflict_service import detect_conflicts

router = APIRouter()


@router.post("/detect")
async def detect_conflict_api(body: ConflictDetectReq, db: AsyncSession = Depends(get_db)):
    """冲突检测"""
    conflicts = await detect_conflicts(
        db, content=body.content, tags=body.tags,
        owner_id=body.owner_id, scope=body.scope,
        exclude_id=body.exclude_id,
    )
    return ok({
        "has_conflict": len(conflicts) > 0,
        "conflicts": conflicts,
    })
