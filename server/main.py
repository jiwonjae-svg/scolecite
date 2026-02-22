# =============================================================================
# Project Scolecite - FastAPI Application Entry Point
# DISCLAIMER: For educational/research purposes only.
# The authors are NOT responsible for any financial losses.
# =============================================================================
"""
FastAPI server for the AI trading bot.
Run locally: uvicorn server.main:app --host 0.0.0.0 --port 8000 --reload
Cloud Run: see Dockerfile.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Ensure project root is on the path so `shared` and `server` are importable
_project_root = Path(__file__).resolve().parent.parent
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

from shared.config import get_settings
from server.database import init_db
from server.core.orchestrator import Orchestrator
from server.routers.api import router
from server.utils.logging import setup_logging, register_thought_callback

settings = get_settings()


# ---------------------------------------------------------------------------
# Application lifespan (startup / shutdown)
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB, logging, orchestrator. Shutdown: stop bot."""
    # Logging
    setup_logging()

    # Database tables
    await init_db()

    # Orchestrator singleton
    orch = Orchestrator()
    app.state.orchestrator = orch

    # Register thought-process callback to push logs via SSE
    async def _thought_to_sse(log_entry: dict) -> None:
        await orch._push_sse("log", log_entry)
        # Also persist to DB
        await orch._save_thought(
            agent=log_entry.get("agent", "unknown"),
            action=log_entry.get("action", ""),
            thought=log_entry.get("thought", ""),
            data=log_entry.get("data"),
        )

    register_thought_callback(_thought_to_sse)

    yield  # app is running

    # Shutdown
    if orch.status.value == "running":
        await orch.stop()


# ---------------------------------------------------------------------------
# FastAPI App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Project Scolecite — AI Trading Bot",
    description=(
        "Autonomous AI trading system for US stock markets. "
        "For educational/research purposes only."
    ),
    version="0.1.0",
    lifespan=lifespan,
)

# CORS (allow desktop client and dev tools)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(router)


# ---------------------------------------------------------------------------
# Health / Readiness / Startup Probes (Cloud Run)
# ---------------------------------------------------------------------------
@app.get("/health")
async def health():
    """Liveness probe — lightweight, no DB call."""
    return {"status": "ok", "mode": settings.TRADING_MODE}


@app.get("/ready")
async def readiness():
    """Readiness probe — verifies DB connection is alive."""
    from server.database import async_session_factory
    from sqlalchemy import text

    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        return {"status": "ready", "db": "connected"}
    except Exception as exc:
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=503,
            content={"status": "not_ready", "db": str(exc)},
        )


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "server.main:app",
        host=settings.SERVER_HOST,
        port=settings.SERVER_PORT,
        reload=True,
        log_level="info",
    )
