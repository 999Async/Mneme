from fastapi import APIRouter

router = APIRouter()


@router.post("/detect")
async def detect_conflict(body: dict):
    """冲突检测"""
    # TODO
    return {"ok": True, "data": {"has_conflict": False, "conflicts": []}}
