from fastapi import APIRouter

router = APIRouter()


@router.get("/{memory_id}")
async def get_memory(memory_id: str):
    """获取单条记忆详情"""
    # TODO
    return {"ok": True, "data": {}}


@router.get("/{memory_id}/history")
async def get_memory_history(memory_id: str):
    """获取记忆版本历史"""
    # TODO
    return {"ok": True, "data": {"current": {}, "history": []}}


@router.post("/{memory_id}/review")
async def review_memory(memory_id: str, body: dict):
    """复习记忆：review / dismiss / done"""
    # TODO
    return {"ok": True, "data": {}}
