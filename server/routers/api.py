# =============================================================================
# Project Scolecite - FastAPI Routers
# DISCLAIMER: For educational/research purposes only.
# The authors are NOT responsible for any financial losses.
# =============================================================================
"""
REST + SSE endpoints exposed by the trading bot server.

REST endpoints:
 - GET  /api/status          → dashboard status
 - POST /api/bot/start       → start the AI loop
 - POST /api/bot/stop        → stop the AI loop
 - POST /api/bot/emergency   → emergency kill switch
 - POST /api/bot/reset       → reset emergency state
 - GET  /api/portfolio       → current portfolio
 - GET  /api/trades          → trade history
 - GET  /api/strategy        → current strategy
 - GET  /api/insights        → latest AI insights
 - GET  /api/reviews         → self-correction reviews
 - POST /api/chat            → AI chat with Opus CEO
 - GET  /api/journal         → trade journal entries
 - GET  /api/candles         → multi-timeframe candles
 - GET  /api/universe        → dynamic ticker universe
 - GET  /api/cost            → AI cost summary
 - GET  /api/ticker-cards    → live ticker card summaries
 - GET  /api/risk-status     → risk manager state

MCP endpoints:
 - GET  /mcp/tools           → list MCP tools
 - GET  /mcp/resources       → list MCP resources
 - POST /mcp/call            → call an MCP tool
 - POST /mcp/read            → read an MCP resource

SSE:
 - GET  /api/stream          → Server-Sent Events stream
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Request, Query
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from shared.schemas import BotStatus, ChatRequest, Timeframe
from server.core.ai_clients import get_cost_summary, sanitize_user_input
from server.core.settings_manager import get_settings_manager
from server.database import async_session_factory, JournalRecord

router = APIRouter()


def _get_orchestrator(request: Request):
    """Retrieve the Orchestrator singleton from app state."""
    return request.app.state.orchestrator


# ---------------------------------------------------------------------------
# Dashboard Status
# ---------------------------------------------------------------------------
@router.get("/api/status")
async def get_status(request: Request) -> dict:
    orch = _get_orchestrator(request)
    portfolio = orch.latest_portfolio
    risk_status = orch.risk_manager.get_status()
    cost = await get_cost_summary()
    return {
        "bot_status": orch.status.value,
        "trading_mode": orch.engine._base_url,
        "strategy_version": orch.strategy_version,
        "current_strategy": (
            orch.current_strategy.reasoning[:300]
            if orch.current_strategy
            else None
        ),
        "portfolio": portfolio.model_dump(mode="json") if portfolio else None,
        "tracked_symbols": orch.tracked_symbols,
        "risk": {
            "is_paused": orch.risk_manager.is_paused,
            "is_emergency": orch.risk_manager.is_emergency,
        },
        "is_rest_mode": risk_status.get("rest_mode", False),
        "consecutive_losses": risk_status.get("consecutive_stop_losses", 0),
        "ai_cost_today": cost.total_cost_usd,
        "ticker_cards": [c.model_dump(mode="json") for c in orch.ticker_cards],
    }


# ---------------------------------------------------------------------------
# Bot Controls
# ---------------------------------------------------------------------------
@router.post("/api/bot/start")
async def bot_start(request: Request) -> dict:
    orch = _get_orchestrator(request)
    if orch.status == BotStatus.RUNNING:
        return {"message": "Bot is already running"}
    await orch.start()
    return {"message": "Bot started", "status": orch.status.value}


@router.post("/api/bot/stop")
async def bot_stop(request: Request) -> dict:
    orch = _get_orchestrator(request)
    await orch.stop()
    return {"message": "Bot stopped", "status": orch.status.value}


@router.post("/api/bot/emergency")
async def bot_emergency(request: Request) -> dict:
    orch = _get_orchestrator(request)
    results = await orch.emergency_stop()
    return {
        "message": "EMERGENCY STOP executed",
        "liquidated": results,
        "status": orch.status.value,
    }


@router.post("/api/bot/reset")
async def bot_reset(request: Request) -> dict:
    orch = _get_orchestrator(request)
    orch.risk_manager.reset_emergency()
    orch.status = BotStatus.STOPPED
    return {"message": "Emergency state reset", "status": orch.status.value}


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------
@router.get("/api/portfolio")
async def get_portfolio(request: Request) -> dict:
    orch = _get_orchestrator(request)
    portfolio = await orch.engine.get_portfolio()
    return portfolio.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Trade History
# ---------------------------------------------------------------------------
@router.get("/api/trades")
async def get_trades(request: Request, limit: int = 50) -> list:
    orch = _get_orchestrator(request)
    return await orch.engine.get_trade_history(limit)


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
@router.get("/api/strategy")
async def get_strategy(request: Request) -> dict:
    orch = _get_orchestrator(request)
    if orch.current_strategy:
        return {
            "version": orch.current_strategy.version,
            "reasoning": orch.current_strategy.reasoning,
            "risk_notes": orch.current_strategy.risk_notes,
            "confidence": orch.current_strategy.confidence,
            "self_correction": orch.current_strategy.self_correction,
            "cost_usd": orch.current_strategy.cost_usd,
            "hypothesis_accepted": orch.current_strategy.hypothesis_accepted,
            "hypothesis_rejected": orch.current_strategy.hypothesis_rejected,
            "timestamp": str(orch.current_strategy.timestamp),
        }
    return {"message": "No strategy available yet"}


# ---------------------------------------------------------------------------
# AI Insights
# ---------------------------------------------------------------------------
@router.get("/api/insights")
async def get_insights(request: Request) -> list:
    orch = _get_orchestrator(request)
    return [i.model_dump(mode="json") for i in orch.latest_insights]


# ---------------------------------------------------------------------------
# Reviews / Self-Correction
# ---------------------------------------------------------------------------
@router.get("/api/reviews")
async def get_reviews(request: Request) -> list:
    orch = _get_orchestrator(request)
    return await orch._get_recent_reviews()


# ---------------------------------------------------------------------------
# AI Chat (Opus CEO)
# ---------------------------------------------------------------------------
@router.post("/api/chat")
async def chat(request: Request) -> dict:
    body = await request.json()
    message = body.get("message", "")
    if not message:
        return {"error": "Message is required"}

    # Sanitize for prompt injection
    safe_message = sanitize_user_input(message)

    orch = _get_orchestrator(request)
    response = await orch.chat(safe_message)
    return {"response": response}


# ---------------------------------------------------------------------------
# Trade Journal
# ---------------------------------------------------------------------------
@router.get("/api/journal")
async def get_journal(request: Request, limit: int = 30) -> list:
    try:
        async with async_session_factory() as session:
            stmt = (
                select(JournalRecord)
                .order_by(JournalRecord.created_at.desc())
                .limit(limit)
            )
            result = await session.execute(stmt)
            records = result.scalars().all()
            return [
                {
                    "id": r.id,
                    "date": r.date,
                    "summary": r.summary,
                    "trades_count": r.trades_count,
                    "wins": r.wins,
                    "losses": r.losses,
                    "pnl": r.pnl,
                    "lessons": r.get_lessons(),
                    "market_conditions": r.market_conditions,
                    "created_at": str(r.created_at),
                }
                for r in records
            ]
    except Exception as e:
        return [{"error": str(e)}]


# ---------------------------------------------------------------------------
# Multi-Timeframe Candles
# ---------------------------------------------------------------------------
_TF_UI_MAP: dict[str, Timeframe] = {
    "1min": Timeframe.MIN_1,
    "5min": Timeframe.MIN_5,
    "15min": Timeframe.MIN_15,
    "1h": Timeframe.HOUR_1,
    "1d": Timeframe.DAY_1,
    "1w": Timeframe.WEEK_1,
    "1mo": Timeframe.MONTH_1,
    "1y": Timeframe.YEAR_1,
}


@router.get("/api/candles")
async def get_candles(
    request: Request,
    symbol: str = Query(..., description="Stock ticker"),
    timeframe: str = Query("1h", description="Timeframe: 1min,5min,15min,1h,1d,1w,1mo,1y"),
    limit: int = Query(100, description="Max candles"),
) -> dict:
    orch = _get_orchestrator(request)
    try:
        tf = _TF_UI_MAP.get(timeframe) or Timeframe(timeframe)
        candles = await orch.engine.get_candles(symbol.upper(), tf, limit)
        return {
            "symbol": symbol.upper(),
            "timeframe": timeframe,
            "count": len(candles),
            "candles": [c.model_dump(mode="json") for c in candles],
        }
    except ValueError:
        return {"error": f"Invalid timeframe: {timeframe}"}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Ticker Search (in-memory, loaded at startup)
# ---------------------------------------------------------------------------
@router.get("/api/tickers/search")
async def search_tickers(
    request: Request,
    q: str = Query("", description="Ticker prefix to search"),
    limit: int = Query(20, description="Max results"),
) -> dict:
    orch = _get_orchestrator(request)
    results = orch.engine.search_tickers(q, limit)
    return {"results": results, "count": len(results)}


# ---------------------------------------------------------------------------
# Dynamic Universe
# ---------------------------------------------------------------------------
@router.get("/api/universe")
async def get_universe(request: Request) -> dict:
    orch = _get_orchestrator(request)
    return {"symbols": orch.tracked_symbols}


@router.post("/api/universe")
async def set_universe(request: Request) -> dict:
    body = await request.json()
    symbols = body.get("symbols", [])
    orch = _get_orchestrator(request)
    orch.tracked_symbols = [s.upper() for s in symbols]
    orch.mcp.update_universe(orch.tracked_symbols)
    return {"symbols": orch.tracked_symbols}


# ---------------------------------------------------------------------------
# AI Cost Summary
# ---------------------------------------------------------------------------
@router.get("/api/cost")
async def get_ai_cost(request: Request) -> dict:
    cost = await get_cost_summary()
    return cost.model_dump(mode="json")


# ---------------------------------------------------------------------------
# Ticker Cards
# ---------------------------------------------------------------------------
@router.get("/api/ticker-cards")
async def get_ticker_cards(request: Request) -> list:
    orch = _get_orchestrator(request)
    return [c.model_dump(mode="json") for c in orch.ticker_cards]


# ---------------------------------------------------------------------------
# Risk Status
# ---------------------------------------------------------------------------
@router.get("/api/risk-status")
async def get_risk_status(request: Request) -> dict:
    orch = _get_orchestrator(request)
    return orch.risk_manager.get_status()


# ---------------------------------------------------------------------------
# MCP Endpoints
# ---------------------------------------------------------------------------
@router.get("/mcp/tools")
async def mcp_list_tools(request: Request) -> list:
    orch = _get_orchestrator(request)
    return orch.mcp.list_tools()


@router.get("/mcp/resources")
async def mcp_list_resources(request: Request) -> list:
    orch = _get_orchestrator(request)
    return orch.mcp.list_resources()


@router.post("/mcp/call")
async def mcp_call_tool(request: Request) -> dict:
    body = await request.json()
    name = body.get("name", "")
    arguments = body.get("arguments", {})
    orch = _get_orchestrator(request)
    result = await orch.mcp.call_tool(name, arguments)
    return {"result": result}


@router.post("/mcp/read")
async def mcp_read_resource(request: Request) -> dict:
    body = await request.json()
    uri = body.get("uri", "")
    orch = _get_orchestrator(request)
    result = await orch.mcp.read_resource(uri)
    return {"result": result}


# ---------------------------------------------------------------------------
# Runtime Settings (settings.json)
# ---------------------------------------------------------------------------
@router.get("/api/settings")
async def get_settings_all(request: Request) -> dict:
    mgr = get_settings_manager()
    return {"settings": mgr.get_all(), "synced": True}


@router.put("/api/settings")
async def update_settings(request: Request) -> dict:
    body = await request.json()
    patch = body.get("patch", body)
    mgr = get_settings_manager()

    # Hot-reload orchestrator-visible values
    ok, errors = mgr.update(patch)
    if not ok:
        return {"ok": False, "errors": errors}

    # Propagate runtime-critical values to the orchestrator
    orch = _get_orchestrator(request)
    _apply_runtime_settings(orch, mgr.get_all())
    return {"ok": True, "settings": mgr.get_all()}


@router.post("/api/settings/reset")
async def reset_settings(request: Request) -> dict:
    mgr = get_settings_manager()
    new = mgr.reset_defaults()
    orch = _get_orchestrator(request)
    _apply_runtime_settings(orch, new)
    return {"ok": True, "settings": new}


@router.get("/api/settings/defaults")
async def get_defaults(request: Request) -> dict:
    mgr = get_settings_manager()
    return {"defaults": mgr.get_defaults()}


def _apply_runtime_settings(orch: Any, data: dict) -> None:
    """Push runtime settings into the orchestrator / risk-manager
    without a full server restart."""
    # 1. Refresh the config.py singleton so every module sees new values
    try:
        from shared.config import refresh_runtime
        refresh_runtime()
    except Exception:
        pass

    # 2. Push into risk-manager in-memory state
    try:
        from shared.config import get_settings
        cfg = get_settings()

        # Risk manager values
        rm = orch.risk_manager
        rm.max_position_pct = data.get("max_position_percent", cfg.MAX_POSITION_PERCENT)
        rm.max_drawdown_pct = data.get("max_drawdown_percent", cfg.MAX_DRAWDOWN_PERCENT)
        rm.daily_loss_limit_pct = data.get("daily_loss_limit_percent", cfg.DAILY_LOSS_LIMIT_PERCENT)
        rm.consecutive_sl_limit = int(data.get("consecutive_stop_loss_pause", cfg.CONSECUTIVE_STOP_LOSS_PAUSE))
        rm.vix_panic = data.get("vix_panic_threshold", cfg.VIX_PANIC_THRESHOLD)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# API Key Submission (hybrid: input in UI → store on server → mask)
# ---------------------------------------------------------------------------
@router.post("/api/keys")
async def submit_api_keys(request: Request) -> dict:
    """Accept API keys from the UI, write them to .env, then mask."""
    from pathlib import Path
    import re

    body = await request.json()
    env_file = Path(__file__).resolve().parent.parent.parent / ".env"

    if not env_file.exists():
        return {"ok": False, "error": ".env file not found on server"}

    content = env_file.read_text("utf-8")
    updated_keys: list[str] = []

    allowed = {
        "ANTHROPIC_API_KEY", "XAI_GROK_API_KEY", "POLYGON_API_KEY",
        "APCA_API_KEY_ID", "APCA_API_SECRET_KEY",
    }

    for key, value in body.items():
        if key not in allowed:
            continue
        value = str(value).strip()
        if not value:
            continue
        # Replace or append
        pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)
        if pattern.search(content):
            content = pattern.sub(f"{key}={value}", content)
        else:
            content += f"\n{key}={value}\n"
        updated_keys.append(key)

    env_file.write_text(content, "utf-8")
    return {"ok": True, "updated": updated_keys}


@router.get("/api/keys/status")
async def get_key_status(request: Request) -> dict:
    """Return masked status of each API key (last 4 chars only)."""
    from shared.config import get_settings
    cfg = get_settings()

    def _mask(val: str) -> str:
        if not val or len(val) < 5:
            return "Not set"
        return f"Loaded (***…{val[-4:]})"

    return {
        "ANTHROPIC_API_KEY": _mask(cfg.ANTHROPIC_API_KEY),
        "XAI_GROK_API_KEY": _mask(cfg.XAI_GROK_API_KEY),
        "POLYGON_API_KEY": _mask(cfg.POLYGON_API_KEY),
        "APCA_API_KEY_ID": _mask(cfg.APCA_API_KEY_ID),
        "APCA_API_SECRET_KEY": _mask(cfg.APCA_API_SECRET_KEY),
    }


# ---------------------------------------------------------------------------
# SSE Stream
# ---------------------------------------------------------------------------
@router.get("/api/stream")
async def sse_stream(request: Request):
    """
    Server-Sent Events endpoint.
    Pushes real-time market data, insights, strategy updates, trades, logs.
    HTTP/SSE streaming (no WebSocket; compatible with Nginx proxy).
    """
    orch = _get_orchestrator(request)
    queue = orch.subscribe_sse()

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield {
                        "event": msg["event"],
                        "data": json.dumps(msg["data"], default=str),
                    }
                except asyncio.TimeoutError:
                    yield {"event": "heartbeat", "data": "{}"}
        finally:
            orch.unsubscribe_sse(queue)

    return EventSourceResponse(event_generator())


# ---------------------------------------------------------------------------
# Tracked Symbols (legacy — use /api/universe instead)
# ---------------------------------------------------------------------------
@router.get("/api/symbols")
async def get_symbols(request: Request) -> list:
    orch = _get_orchestrator(request)
    return orch.tracked_symbols


@router.post("/api/symbols")
async def set_symbols(request: Request) -> dict:
    body = await request.json()
    symbols = body.get("symbols", [])
    orch = _get_orchestrator(request)
    orch.tracked_symbols = [s.upper() for s in symbols]
    return {"symbols": orch.tracked_symbols}
