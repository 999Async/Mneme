from fastapi import APIRouter

router = APIRouter()


@router.post("")
async def create_memory(body: dict):
    """创建记忆"""
    # TODO
    return {"ok": True, "data": {}}


@router.get("")
async def list_memories(
    owner_id: str = "",
    scope: str | None = None,
    type: str | None = None,
    keywords: str | None = None,
    active_only: bool = True,
    page: int = 1,
    page_size: int = 20,
):
    """查询记忆（三路召回）"""
    # TODO
    return {"ok": True, "data": {"total": 0, "items": []}}


@router.delete("/{memory_id}")
async def delete_memory(memory_id: str, user_id: str = ""):
    """软删除记忆"""
    # TODO
    return {"ok": True, "data": None}
