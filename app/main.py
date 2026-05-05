import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from sqlalchemy import text
from fastapi.responses import JSONResponse

from app.config import settings

# 配置日志级别
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# 设置 uvicorn 的日志级别
logging.getLogger("uvicorn").setLevel(logging.INFO)
logging.getLogger("uvicorn.access").setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create DB tables if not exist
    from app.db.session import engine
    from app.models import Base

    async with engine.begin() as conn:
        # Enable pgvector extension for PostgreSQL
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)

    # 清理无效的buffer数据（防止测试数据残留）
    try:
        from app.cache import redis_client
        from app.services.message_buffer_service import _buffer_key, _ts_key
        import json

        # 扫描所有buffer keys
        keys = await redis_client.scan_keys("mneme:buffer:*")
        # 排除时间戳keys（mneme:buffer:ts:*）
        buffer_keys = [k for k in keys if not k.endswith(":ts") and ":ts:" not in k]

        for buffer_key in buffer_keys:
            try:
                # 获取所有数据并验证
                raw = await redis_client.lrange(buffer_key, 0, -1)
                if not raw:
                    continue

                malformed_items = []
                for item in raw:
                    try:
                        json.loads(item)
                    except (json.JSONDecodeError, TypeError):
                        malformed_items.append(item)

                # 如果有无效数据，清理整个buffer
                if malformed_items:
                    owner_id = buffer_key.replace("mneme:buffer:", "")
                    logging.warning(f"Cleaning malformed buffer data for {owner_id}: {len(malformed_items)} items")
                    await redis_client.delete(buffer_key)
                    await redis_client.delete(_ts_key(owner_id))
            except Exception as e:
                logging.error(f"Error cleaning buffer key {buffer_key}: {e}")

        if buffer_keys:
            logging.info(f"Validated {len(buffer_keys)} buffer keys at startup")
    except Exception as e:
        logging.warning(f"Buffer cleanup failed (continuing anyway): {e}")

    yield


app = FastAPI(
    title="Mneme Memory Service",
    version="0.1.0",
    lifespan=lifespan,
)


# Auth middleware
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # Skip auth for health check and docs, or when no token configured
    if request.url.path in ("/health", "/docs", "/openapi.json", "/redoc", "/api/callbacks/card-action"):
        return await call_next(request)
    if not settings.mneme_service_token:
        return await call_next(request)

    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if settings.mneme_service_token and token != settings.mneme_service_token:
        return JSONResponse(status_code=401, content={"ok": False, "error": "Unauthorized"})
    return await call_next(request)


# Health check
@app.get("/health")
async def health():
    return {"ok": True}


# Register routers
from app.api.events import router as events_router  # noqa: E402
from app.api.memories import router as memories_router  # noqa: E402
from app.api.memory_detail import router as memory_detail_router  # noqa: E402
from app.api.conflicts import router as conflicts_router  # noqa: E402
from app.api.context_snapshots import router as snapshots_router  # noqa: E402
from app.api.cron import router as cron_router  # noqa: E402
from app.api.push_logs import router as push_logs_router  # noqa: E402
from app.api.memory_logs import router as memory_logs_router  # noqa: E402
from app.api.callbacks import router as callbacks_router  # noqa: E402

app.include_router(events_router, prefix="/api/events", tags=["events"])
app.include_router(memories_router, prefix="/api/memories", tags=["memories"])
app.include_router(memory_detail_router, prefix="/api/memories", tags=["memory-detail"])
app.include_router(conflicts_router, prefix="/api/memories/conflicts", tags=["conflicts"])
app.include_router(snapshots_router, prefix="/api/context-snapshots", tags=["context-snapshots"])
app.include_router(cron_router, prefix="/api/cron", tags=["cron"])
app.include_router(push_logs_router, prefix="/api/push-logs", tags=["push-logs"])
app.include_router(memory_logs_router, prefix="/api/memory-logs", tags=["memory-logs"])
app.include_router(callbacks_router, prefix="/api/callbacks", tags=["callbacks"])
