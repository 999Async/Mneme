from fastapi import APIRouter

router = APIRouter()


@router.post("/decay")
async def cron_decay(body: dict | None = None):
    """遗忘曲线衰减扫描（每30分钟 Cron 触发）"""
    # TODO
    return {"ok": True, "data": {"scanned": 0, "decayed": 0}}


@router.post("/push-l1")
async def cron_push_l1(body: dict | None = None):
    """L1 推送执行"""
    # TODO
    return {"ok": True, "data": {"pushed": 0}}


@router.get("/push-l2/candidates")
async def get_l2_candidates(
    current_message: str = "",
    current_snapshot: str = "",
    chat_id: str = "",
    threshold: float = 0.75,
):
    """获取 L2 候选记忆"""
    # TODO
    return {"ok": True, "data": {"candidates": []}}
