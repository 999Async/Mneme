from fastapi import APIRouter

router = APIRouter()


@router.post("")
async def create_snapshot(body: dict):
    """保存上下文快照（心流恢复）"""
    # TODO
    return {"ok": True, "data": {}}


@router.get("/latest")
async def get_latest_snapshot(user_id: str = ""):
    """获取最新上下文快照"""
    # TODO
    return {"ok": True, "data": {}}


@router.delete("/latest")
async def delete_latest_snapshot(user_id: str = ""):
    """清除最新上下文快照"""
    # TODO
    return {"ok": True, "data": None}
