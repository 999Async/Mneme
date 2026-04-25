from fastapi import APIRouter

router = APIRouter()


@router.post("")
async def create_memory_log(body: dict):
    """记录记忆操作日志"""
    # TODO
    return {"ok": True, "data": {}}
