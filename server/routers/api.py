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
    cost = get_cost_summary()
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
        "ai_cost_today": cost.total_today,
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
@router.get("/api/candles")
async def get_candles(
    request: Request,
    symbol: str = Query(..., description="Stock ticker"),
    timeframe: str = Query("1h", description="Timeframe: 1min,5min,15min,1h,1d,1w,1mo"),
    limit: int = Query(100, description="Max candles"),
) -> dict:
    orch = _get_orchestrator(request)
    try:
        tf = Timeframe(timeframe)
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
    cost = get_cost_summary()
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
# SSE Stream
# ---------------------------------------------------------------------------
@router.get("/api/stream")
async def sse_stream(request: Request):
    """
    Server-Sent Events endpoint.
    Pushes real-time market data, insights, strategy updates, trades, logs.
    Cloud Run compatible (HTTP/SSE, no WebSocket).
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
