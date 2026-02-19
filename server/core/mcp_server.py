# =============================================================================
# Project Scolecite - MCP Server (Model Control Protocol)
# DISCLAIMER: For educational/research purposes only.
# The authors are NOT responsible for any financial losses.
# =============================================================================
"""
MCP layer integrated into FastAPI.
Exposes Tools and Resources that Claude Opus (CEO) can call via tool_use.

Tools:
 - get_ai_insights    → invoke Grok Fast for fresh data
 - perform_trade      → execute an order (risk-checked)
 - get_portfolio_status → retrieve positions & P&L
 - get_risk_status    → risk manager state (rest mode, fatigue, etc.)
 - get_ai_cost_summary → daily AI budget usage
 - get_universe       → current dynamic ticker universe

Resources (URI-addressable, read-only):
 - mcp://trading/market_data  → latest market snapshots
 - mcp://trading/logs         → AI thought-process logs
 - mcp://trading/history      → trade history + reviews
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import select

from shared.config import get_settings
from shared.schemas import (
    MCPToolDefinition,
    MCPResourceDefinition,
    TradeRequest,
    TradeSide,
    OrderType,
    Timeframe,
)
from server.core.ai_clients import GrokFastClient, get_cost_summary
from server.core.trading_engine import TradingEngine
from server.core.risk_manager import RiskManager
from server.database import async_session_factory, ThoughtRecord, TradeRecord, ReviewRecord
from server.utils.logging import broadcast_thought, get_logger

logger = get_logger("mcp_server")
settings = get_settings()


# ---------------------------------------------------------------------------
# Tool & Resource Definitions
# ---------------------------------------------------------------------------
TOOL_DEFINITIONS: list[MCPToolDefinition] = [
    MCPToolDefinition(
        name="get_ai_insights",
        description=(
            "Invoke Grok Fast (news/technical/social analysis) "
            "to collect fresh insights on a stock symbol."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "enum": ["news", "technical", "social"],
                    "description": "Type of analysis to request",
                },
                "symbol": {
                    "type": "string",
                    "description": "Stock ticker (e.g. AAPL)",
                },
                "raw_data": {
                    "type": "string",
                    "description": "Optional raw text to analyse",
                },
            },
            "required": ["task", "symbol"],
        },
    ),
    MCPToolDefinition(
        name="perform_trade",
        description=(
            "Execute a buy or sell order. All risk checks "
            "(balance, slippage, position size, drawdown, fatigue) "
            "are enforced automatically."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "side": {"type": "string", "enum": ["buy", "sell"]},
                "qty": {"type": "number"},
                "order_type": {
                    "type": "string",
                    "enum": ["market", "limit"],
                    "default": "market",
                },
                "limit_price": {"type": "number"},
                "reason": {"type": "string"},
                "confidence": {
                    "type": "number",
                    "description": "Confidence score 0.0-1.0 for position sizing",
                },
            },
            "required": ["symbol", "side", "qty"],
        },
    ),
    MCPToolDefinition(
        name="get_portfolio_status",
        description="Retrieve current portfolio: equity, cash, positions, P&L breakdown.",
        input_schema={"type": "object", "properties": {}},
    ),
    MCPToolDefinition(
        name="get_risk_status",
        description=(
            "Get current risk manager status: rest mode, consecutive losses, "
            "daily trade counts, drawdown level."
        ),
        input_schema={"type": "object", "properties": {}},
    ),
    MCPToolDefinition(
        name="get_ai_cost_summary",
        description="Get today's AI API usage cost breakdown and remaining budget.",
        input_schema={"type": "object", "properties": {}},
    ),
    MCPToolDefinition(
        name="get_candles",
        description="Fetch multi-timeframe candle data for a symbol.",
        input_schema={
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Stock ticker"},
                "timeframe": {
                    "type": "string",
                    "enum": ["1min", "5min", "15min", "1h", "1d", "1w", "1mo"],
                    "default": "1h",
                },
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["symbol"],
        },
    ),
]

RESOURCE_DEFINITIONS: list[MCPResourceDefinition] = [
    MCPResourceDefinition(
        uri="mcp://trading/market_data",
        name="Market Data",
        description="Latest price snapshots and candle data for tracked symbols.",
    ),
    MCPResourceDefinition(
        uri="mcp://trading/logs",
        name="AI Thought Logs",
        description="AI thought-process log entries (structured JSON).",
    ),
    MCPResourceDefinition(
        uri="mcp://trading/history",
        name="Trade History & Reviews",
        description="Past trades and Opus self-correction review entries.",
    ),
]


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
class MCPServer:
    """
    MCP server integrated into FastAPI.
    Handles tool dispatch and resource resolution for Opus CEO.
    """

    def __init__(
        self,
        grok_fast: GrokFastClient,
        engine: TradingEngine,
        risk_manager: RiskManager,
    ) -> None:
        self.grok_fast = grok_fast
        self.engine = engine
        self.risk_manager = risk_manager

        # Cache for market snapshots (updated by orchestrator)
        self._market_cache: dict[str, dict] = {}

        # Dynamic universe list (updated by orchestrator)
        self._universe: list[str] = []

    # ------------------------------------------------------------------
    # Tool listing (POST /mcp/tools)
    # ------------------------------------------------------------------
    def list_tools(self) -> list[dict]:
        return [t.model_dump() for t in TOOL_DEFINITIONS]

    def list_resources(self) -> list[dict]:
        return [r.model_dump() for r in RESOURCE_DEFINITIONS]

    # ------------------------------------------------------------------
    # Tool Dispatch  (called by Opus via tool_use)
    # ------------------------------------------------------------------
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """
        Dispatch a tool call.
        Every tool passes through risk / safety checks before execution.
        """
        await broadcast_thought(
            "mcp", "tool_dispatch",
            f"Dispatching MCP tool: {name}",
            {"arguments": arguments},
        )

        handlers = {
            "get_ai_insights": self._tool_get_insights,
            "perform_trade": self._tool_perform_trade,
            "get_portfolio_status": self._tool_get_portfolio,
            "get_risk_status": self._tool_get_risk_status,
            "get_ai_cost_summary": self._tool_get_cost_summary,
            "get_candles": self._tool_get_candles,
        }

        handler = handlers.get(name)
        if handler:
            return await handler(arguments)
        return {"error": f"Unknown tool: {name}"}

    # ------------------------------------------------------------------
    # Resource Resolution
    # ------------------------------------------------------------------
    async def read_resource(self, uri: str) -> Any:
        """Resolve an mcp:// URI and return the data."""
        await broadcast_thought("mcp", "resource_read", f"Reading resource: {uri}")

        if uri == "mcp://trading/market_data":
            return self._market_cache

        elif uri == "mcp://trading/logs":
            return await self._resource_logs()

        elif uri == "mcp://trading/history":
            return await self._resource_history()

        return {"error": f"Unknown resource URI: {uri}"}

    def update_market_cache(self, symbol: str, data: dict) -> None:
        """Called by orchestrator to keep market cache fresh."""
        self._market_cache[symbol] = data

    def update_universe(self, symbols: list[str]) -> None:
        """Called by orchestrator to update dynamic universe."""
        self._universe = symbols

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------
    async def _tool_get_insights(self, args: dict) -> dict:
        task = args.get("task", "news")
        symbol = args.get("symbol", "")
        raw_data = args.get("raw_data", "")

        try:
            if task == "news":
                result = await self.grok_fast.summarise_news(
                    raw_data or "No data provided", symbol,
                )
            elif task == "technical":
                market = self._market_cache.get(symbol, {})
                result = await self.grok_fast.analyse_technical(market, symbol)
            elif task == "social":
                result = await self.grok_fast.analyse_social(
                    raw_data or "No data provided", symbol,
                )
            else:
                return {"error": f"Unknown task: {task}"}

            return result.model_dump(mode="json")
        except Exception as e:
            logger.error("tool_get_insights_failed", error=str(e))
            return {"error": str(e)}

    async def _tool_perform_trade(self, args: dict) -> dict:
        """Execute trade with full risk checks."""
        try:
            symbol = args["symbol"]
            side = TradeSide(args["side"])
            qty = float(args["qty"])
            order_type = OrderType(args.get("order_type", "market"))
            limit_price = args.get("limit_price")
            reason = args.get("reason", "Opus CEO strategy decision")
            confidence = args.get("confidence")

            # Adjust quantity based on confidence if provided
            if confidence is not None:
                qty = self.risk_manager.adjust_qty_for_confidence(qty, confidence)

            request = TradeRequest(
                symbol=symbol,
                side=side,
                qty=qty,
                order_type=order_type,
                limit_price=limit_price,
                reason=reason,
                confidence=confidence,
            )

            # Get current portfolio and price for risk checks
            portfolio = await self.engine.get_portfolio()
            current_price = await self.engine.get_latest_price(symbol)

            if current_price <= 0:
                return {"error": f"Cannot get price for {symbol}"}

            result = await self.engine.execute_trade(
                request=request,
                portfolio=portfolio,
                current_price=current_price,
            )
            return result.model_dump(mode="json")

        except Exception as e:
            logger.error("tool_perform_trade_failed", error=str(e))
            return {"error": str(e)}

    async def _tool_get_portfolio(self, _args: dict | None = None) -> dict:
        try:
            portfolio = await self.engine.get_portfolio()
            return portfolio.model_dump(mode="json")
        except Exception as e:
            logger.error("tool_get_portfolio_failed", error=str(e))
            return {"error": str(e)}

    async def _tool_get_risk_status(self, _args: dict | None = None) -> dict:
        """Return current risk manager state."""
        try:
            return self.risk_manager.get_status()
        except Exception as e:
            logger.error("tool_get_risk_status_failed", error=str(e))
            return {"error": str(e)}

    async def _tool_get_cost_summary(self, _args: dict | None = None) -> dict:
        """Return today's AI cost summary."""
        try:
            return get_cost_summary().model_dump(mode="json")
        except Exception as e:
            logger.error("tool_get_cost_summary_failed", error=str(e))
            return {"error": str(e)}

    async def _tool_get_candles(self, args: dict) -> dict:
        """Fetch multi-timeframe candle data."""
        try:
            symbol = args.get("symbol", "")
            tf_str = args.get("timeframe", "1h")
            limit = int(args.get("limit", 50))

            timeframe = Timeframe(tf_str)
            candles = await self.engine.get_candles(symbol, timeframe, limit)
            return {
                "symbol": symbol,
                "timeframe": tf_str,
                "count": len(candles),
                "candles": [c.model_dump(mode="json") for c in candles],
            }
        except Exception as e:
            logger.error("tool_get_candles_failed", error=str(e))
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Resource implementations (read from DB)
    # ------------------------------------------------------------------
    async def _resource_logs(self, limit: int = 100) -> list[dict]:
        try:
            async with async_session_factory() as session:
                stmt = (
                    select(ThoughtRecord)
                    .order_by(ThoughtRecord.created_at.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                records = result.scalars().all()
                return [
                    {
                        "id": r.id,
                        "agent": r.agent,
                        "action": r.action,
                        "thought": r.thought,
                        "data": r.get_data(),
                        "created_at": str(r.created_at),
                    }
                    for r in records
                ]
        except Exception as e:
            return [{"error": str(e)}]

    async def _resource_history(self, limit: int = 100) -> dict:
        try:
            trades = await self.engine.get_trade_history(limit)
            reviews = await self._get_reviews(limit)
            return {"trades": trades, "reviews": reviews}
        except Exception as e:
            return {"error": str(e)}

    async def _get_reviews(self, limit: int = 50) -> list[dict]:
        try:
            async with async_session_factory() as session:
                stmt = (
                    select(ReviewRecord)
                    .order_by(ReviewRecord.created_at.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                records = result.scalars().all()
                return [
                    {
                        "id": r.id,
                        "trade_id": r.trade_id,
                        "symbol": r.symbol,
                        "expected_outcome": r.expected_outcome,
                        "actual_outcome": r.actual_outcome,
                        "error_analysis": r.error_analysis,
                        "improvement": r.improvement,
                        "strategy_version": r.strategy_version,
                        "created_at": str(r.created_at),
                    }
                    for r in records
                ]
        except Exception as e:
            return [{"error": str(e)}]
