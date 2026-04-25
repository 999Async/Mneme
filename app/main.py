from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create DB tables if not exist
    from app.db.session import engine
    from app.models import Base

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(
    title="Mneme Memory Service",
    version="0.1.0",
    lifespan=lifespan,
)


# Auth middleware
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # Skip auth for health check and docs
    if request.url.path in ("/health", "/docs", "/openapi.json", "/redoc"):
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

app.include_router(events_router, prefix="/api/events", tags=["events"])
app.include_router(memories_router, prefix="/api/memories", tags=["memories"])
app.include_router(memory_detail_router, prefix="/api/memories", tags=["memory-detail"])
app.include_router(conflicts_router, prefix="/api/memories/conflicts", tags=["conflicts"])
app.include_router(snapshots_router, prefix="/api/context-snapshots", tags=["context-snapshots"])
app.include_router(cron_router, prefix="/api/cron", tags=["cron"])
app.include_router(push_logs_router, prefix="/api/push-logs", tags=["push-logs"])
app.include_router(memory_logs_router, prefix="/api/memory-logs", tags=["memory-logs"])
