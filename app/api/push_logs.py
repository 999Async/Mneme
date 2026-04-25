from fastapi import APIRouter

router = APIRouter()


@router.post("")
async def create_push_log(body: dict):
    """记录推送历史"""
    # TODO
    return {"ok": True, "data": {}}


@router.get("/check")
async def check_push_status(
    memory_id: str = "",
    target_id: str = "",
    push_type: str = "",
):
    """检查推送防抖状态"""
    # TODO
    return {"ok": True, "data": {"can_push": True}}
