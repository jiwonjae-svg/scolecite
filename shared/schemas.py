# =============================================================================
# Project Scolecite - Shared Pydantic Schemas
# DISCLAIMER: For educational/research purposes only.
# The authors are NOT responsible for any financial losses.
# =============================================================================
"""
Pydantic models shared between server and client.
These define the canonical JSON shapes for every data exchange.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class TradeSide(str, enum.Enum):
    BUY = "buy"
    SELL = "sell"


class OrderType(str, enum.Enum):
    MARKET = "market"
    LIMIT = "limit"


class BotStatus(str, enum.Enum):
    RUNNING = "running"
    PAUSED = "paused"
    STOPPED = "stopped"
    EMERGENCY_STOP = "emergency_stop"
    REST_MODE = "rest_mode"  # market fatigue


class ConnectionState(str, enum.Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    ERROR = "error"


class Timeframe(str, enum.Enum):
    """Supported chart timeframes."""
    MIN_1 = "1Min"
    MIN_5 = "5Min"
    MIN_15 = "15Min"
    HOUR_1 = "1Hour"
    DAY_1 = "1Day"
    WEEK_1 = "1Week"
    MONTH_1 = "1Month"


# ---------------------------------------------------------------------------
# Market Data
# ---------------------------------------------------------------------------
class MarketCandle(BaseModel):
    """Single OHLCV candle."""
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int
    vwap: Optional[float] = None
    timeframe: str = "1Hour"


class MarketSnapshot(BaseModel):
    """Aggregated market data for a symbol."""
    symbol: str
    price: float
    change_pct: float = 0.0
    volume: int = 0
    avg_volume: int = 0
    candles: list[MarketCandle] = []
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# AI Insights
# ---------------------------------------------------------------------------
class SentimentResult(BaseModel):
    """Sentiment analysis output from Grok."""
    source: str  # "twitter", "news", "reddit"
    summary: str
    sentiment_score: float = Field(ge=-1.0, le=1.0)
    key_topics: list[str] = []
    high_impact_keywords: list[str] = []
    raw_snippet: Optional[str] = None
    reliability_score: float = Field(
        default=1.0, ge=0.0, le=1.0,
        description="Source reliability (1.0=verified news, 0.2=anonymous tweet)",
    )
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class AIInsight(BaseModel):
    """Normalised insight from any AI collector."""
    provider: str  # "grok_fast" | "grok_strategy" | "opus"
    category: str  # "news", "social", "technical", "hypothesis", "universe"
    symbol: Optional[str] = None
    summary: str
    data: dict[str, Any] = {}
    sentiment: Optional[SentimentResult] = None
    token_usage: dict[str, int] = {}
    cost_usd: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------
class StrategyDecision(BaseModel):
    """Decision produced by Claude Opus (CEO)."""
    version: int = 1
    reasoning: str
    actions: list[TradeRequest] = []
    risk_notes: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    self_correction: str = ""  # review of previous decisions
    hypothesis_accepted: list[str] = []
    hypothesis_rejected: list[str] = []
    token_usage: dict[str, int] = {}
    cost_usd: float = 0.0
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Trading
# ---------------------------------------------------------------------------
class TradeRequest(BaseModel):
    """Request to execute a trade."""
    symbol: str
    side: TradeSide
    qty: float
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    reason: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


class TradeResult(BaseModel):
    """Result of an executed (or rejected) trade."""
    order_id: str = ""
    symbol: str
    side: TradeSide
    qty: float
    filled_price: Optional[float] = None
    status: str = "pending"  # "filled", "rejected", "error"
    message: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------
class PositionInfo(BaseModel):
    symbol: str
    qty: float
    avg_entry_price: float
    current_price: float
    unrealized_pl: float
    unrealized_pl_pct: float
    market_value: float


class PortfolioStatus(BaseModel):
    equity: float = 0.0
    cash: float = 0.0
    buying_power: float = 0.0
    daily_pl: float = 0.0
    daily_pl_pct: float = 0.0
    total_pl: float = 0.0
    total_pl_pct: float = 0.0
    positions: list[PositionInfo] = []
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Logs / Thought Process
# ---------------------------------------------------------------------------
class ThoughtLog(BaseModel):
    """Structured log entry for AI thought processes."""
    id: Optional[int] = None
    agent: str  # "grok_fast", "grok_strategy", "opus", "risk_manager", "engine"
    action: str
    thought: str
    data: dict[str, Any] = {}
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Self-Correction / Review
# ---------------------------------------------------------------------------
class ReviewEntry(BaseModel):
    """Post-trade review written by Opus."""
    id: Optional[int] = None
    trade_id: str
    symbol: str
    expected_outcome: str
    actual_outcome: str
    error_analysis: str
    improvement: str
    strategy_version: int = 1
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# SSE Event Envelope
# ---------------------------------------------------------------------------
class SSEEvent(BaseModel):
    """Wrapper for Server-Sent Events pushed to the client."""
    event: str
    data: dict[str, Any]


# ---------------------------------------------------------------------------
# MCP Schemas
# ---------------------------------------------------------------------------
class MCPToolDefinition(BaseModel):
    """Definition of an MCP tool for Claude's tool_use."""
    name: str
    description: str
    input_schema: dict[str, Any]


class MCPResourceDefinition(BaseModel):
    """Definition of an MCP resource (URI-addressable data)."""
    uri: str
    name: str
    description: str
    mime_type: str = "application/json"


class MCPToolCall(BaseModel):
    """Incoming tool call from Claude Opus."""
    name: str
    arguments: dict[str, Any] = {}


class MCPToolResult(BaseModel):
    """Response returned to Claude after tool execution."""
    tool_use_id: str
    content: Any
    is_error: bool = False


# ---------------------------------------------------------------------------
# Dynamic Universe / Candidates
# ---------------------------------------------------------------------------
class TickerCandidate(BaseModel):
    """A ticker selected by Grok Fast for the daily universe."""
    symbol: str
    reason: str = ""
    sector: str = ""
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    source: str = ""  # "trending", "earnings", "sector_rotation"


class CandidateList(BaseModel):
    """Daily ticker universe selected by Grok Fast."""
    candidates: list[TickerCandidate] = []
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    valid_until: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Grok Strategy Hypothesis
# ---------------------------------------------------------------------------
class StrategyHypothesis(BaseModel):
    """Hypothesis generated by Grok 4.2 Strategy Brainstormer."""
    hypothesis_id: str
    symbol: str
    direction: TradeSide
    rationale: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    timeframe: str = "intraday"  # "intraday", "swing", "position"
    supporting_data: dict[str, Any] = {}
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# AI Chat
# ---------------------------------------------------------------------------
class ChatMessage(BaseModel):
    """Message in the AI strategy chat."""
    role: str  # "user" | "assistant"
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ChatRequest(BaseModel):
    """Incoming chat request from the UI."""
    message: str
    context: str = "strategy"


class ChatResponse(BaseModel):
    """Chat response from Opus."""
    reply: str
    data: dict[str, Any] = {}
    token_usage: dict[str, int] = {}
    cost_usd: float = 0.0


# ---------------------------------------------------------------------------
# Trade Journal
# ---------------------------------------------------------------------------
class JournalEntry(BaseModel):
    """Auto-generated trade journal entry."""
    id: Optional[int] = None
    date: str  # "2025-01-15"
    summary: str
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    net_pnl: float = 0.0
    key_lessons: list[str] = []
    strategy_version: int = 0
    ai_commentary: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------
class BacktestRequest(BaseModel):
    """Request to run a quick backtest."""
    symbol: str
    strategy_description: str = ""
    lookback_days: int = 30
    initial_capital: float = 100000.0


class BacktestResult(BaseModel):
    """Result from a backtest run."""
    symbol: str
    start_date: str
    end_date: str
    initial_capital: float
    final_capital: float
    total_return_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    trades: list[dict[str, Any]] = []
    equity_curve: list[float] = []
    summary: str = ""


# ---------------------------------------------------------------------------
# Noise Filter
# ---------------------------------------------------------------------------
class NoiseFilterResult(BaseModel):
    """Result of social data noise filtering by Grok."""
    original_count: int = 0
    filtered_count: int = 0
    reliable_sources: list[str] = []
    discarded_sources: list[str] = []
    adjusted_sentiment: float = 0.0


# ---------------------------------------------------------------------------
# Cost Tracking
# ---------------------------------------------------------------------------
class AICostSummary(BaseModel):
    """Daily AI API cost summary."""
    date: str
    total_cost_usd: float = 0.0
    budget_remaining_usd: float = 0.0
    budget_limit_usd: float = 0.0
    calls_by_provider: dict[str, int] = {}
    cost_by_provider: dict[str, float] = {}
    budget_exceeded: bool = False


# ---------------------------------------------------------------------------
# Ticker Card (for UI)
# ---------------------------------------------------------------------------
class TickerCard(BaseModel):
    """Live ticker card data for UI display."""
    symbol: str
    price: float = 0.0
    change_pct: float = 0.0
    volume: int = 0
    sentiment_score: float = 0.0
    ai_signal: str = "neutral"  # "bullish" | "bearish" | "neutral"
    confidence: float = 0.5
    in_portfolio: bool = False
    position_pnl_pct: float = 0.0


# ---------------------------------------------------------------------------
# Dashboard Status (aggregated for UI)
# ---------------------------------------------------------------------------
class DashboardStatus(BaseModel):
    bot_status: BotStatus = BotStatus.STOPPED
    trading_mode: str = "paper"
    connections: dict[str, ConnectionState] = {}
    portfolio: Optional[PortfolioStatus] = None
    current_strategy: Optional[str] = None
    last_strategy_update: Optional[datetime] = None
    recent_trades: list[TradeResult] = []
    recent_insights: list[AIInsight] = []
    recent_logs: list[ThoughtLog] = []
    recent_reviews: list[ReviewEntry] = []
    ai_cost_today: Optional[AICostSummary] = None
    ticker_cards: list[TickerCard] = []
    is_rest_mode: bool = False
    consecutive_losses: int = 0


# Forward-ref update (StrategyDecision references TradeRequest)
StrategyDecision.model_rebuild()
