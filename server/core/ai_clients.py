# =============================================================================
# Project Scolecite - AI Client Wrappers
# DISCLAIMER: For educational/research purposes only.
# The authors are NOT responsible for any financial losses.
# =============================================================================
"""
Wrappers for Grok (Fast & Strategy) and Claude Opus (CEO) with:
 - Automatic retry via tenacity with exponential backoff
 - Token-usage & cost tracking with daily budget cap
 - Structured JSON output
 - Prompt injection defense
 - Social noise filter (source-reliability scoring)

Architecture:
 - GrokFastClient  (Grok 4.1 Fast)  → 24/7 raw data collection
 - GrokStrategyClient (Grok 4.2)    → strategy brainstorming / hypothesis
 - OpusClient (Claude Opus 4.6)     → CEO — final approval / rejection
"""

from __future__ import annotations

import json
import re
import asyncio
from datetime import datetime, date
from typing import Any, Optional

import anthropic
from openai import AsyncOpenAI
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)

from shared.config import get_settings
from shared.schemas import (
    AIInsight,
    SentimentResult,
    StrategyDecision,
    StrategyHypothesis,
    TradeRequest,
    NoiseFilterResult,
    AICostSummary,
)
from server.utils.logging import broadcast_thought, get_logger
from server.database import async_session_factory, AIUsageRecord

from sqlalchemy import select, func as sa_func

logger = get_logger("ai_clients")
settings = get_settings()

# ---------------------------------------------------------------------------
# Cost constants (approximate USD per 1K tokens, update as pricing changes)
# ---------------------------------------------------------------------------
_COST_TABLE = {
    "grok-fast": {"input": 0.003, "output": 0.015},
    "grok-strategy": {"input": 0.005, "output": 0.025},
    "claude-opus": {"input": 0.015, "output": 0.075},
}


def _estimate_cost(model_key: str, input_tokens: int, output_tokens: int) -> float:
    rates = _COST_TABLE.get(model_key, {"input": 0.0, "output": 0.0})
    return (input_tokens / 1000) * rates["input"] + (output_tokens / 1000) * rates["output"]


async def _log_usage(
    provider: str, model: str, input_tokens: int, output_tokens: int, cost: float, purpose: str
) -> None:
    """Persist token usage to the database."""
    try:
        async with async_session_factory() as session:
            record = AIUsageRecord(
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
                purpose=purpose,
            )
            session.add(record)
            await session.commit()
    except Exception as e:
        logger.warning("usage_log_failed", error=str(e))


# ---------------------------------------------------------------------------
# Daily AI Cost Budget
# ---------------------------------------------------------------------------
async def get_daily_cost() -> float:
    """Return total AI spend for today (UTC)."""
    try:
        today = datetime.utcnow().date()
        async with async_session_factory() as session:
            stmt = (
                select(sa_func.sum(AIUsageRecord.cost_usd))
                .where(sa_func.date(AIUsageRecord.created_at) == str(today))
            )
            result = await session.execute(stmt)
            total = result.scalar()
            return float(total or 0.0)
    except Exception:
        return 0.0


async def check_budget() -> bool:
    """Return True if daily budget is NOT exceeded."""
    daily = await get_daily_cost()
    return daily < settings.DAILY_AI_BUDGET_USD


async def get_cost_summary() -> AICostSummary:
    """Build a daily cost summary."""
    today = datetime.utcnow().date()
    total = await get_daily_cost()
    try:
        async with async_session_factory() as session:
            stmt = (
                select(
                    AIUsageRecord.provider,
                    sa_func.count(AIUsageRecord.id),
                    sa_func.sum(AIUsageRecord.cost_usd),
                )
                .where(sa_func.date(AIUsageRecord.created_at) == str(today))
                .group_by(AIUsageRecord.provider)
            )
            result = await session.execute(stmt)
            rows = result.all()
            calls_by = {r[0]: r[1] for r in rows}
            cost_by = {r[0]: float(r[2] or 0) for r in rows}
    except Exception:
        calls_by = {}
        cost_by = {}

    return AICostSummary(
        date=str(today),
        total_cost_usd=total,
        budget_remaining_usd=max(0.0, settings.DAILY_AI_BUDGET_USD - total),
        budget_limit_usd=settings.DAILY_AI_BUDGET_USD,
        calls_by_provider=calls_by,
        cost_by_provider=cost_by,
        budget_exceeded=total >= settings.DAILY_AI_BUDGET_USD,
    )


# ---------------------------------------------------------------------------
# Prompt Injection Defense
# ---------------------------------------------------------------------------
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I),
    re.compile(r"system\s*:\s*you\s+are", re.I),
    re.compile(r"<\|im_start\|>", re.I),
    re.compile(r"IGNORE\s+ABOVE", re.I),
    re.compile(r"override\s+(system|safety)", re.I),
    re.compile(r"jailbreak", re.I),
    re.compile(r"DAN\s+mode", re.I),
]


def sanitize_user_input(text: str) -> str:
    """Strip known prompt-injection patterns from user-supplied text."""
    cleaned = text
    for pat in _INJECTION_PATTERNS:
        cleaned = pat.sub("[FILTERED]", cleaned)
    # Also strip any embedded system-style tags
    cleaned = re.sub(r"</?system>", "", cleaned, flags=re.I)
    return cleaned


# ---------------------------------------------------------------------------
# Safe JSON parser
# ---------------------------------------------------------------------------
def _safe_parse(text: str) -> dict:
    try:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("\n", 1)[-1].rsplit("```", 1)[0]
        return json.loads(cleaned)
    except (json.JSONDecodeError, IndexError):
        return {"raw": text}


def _stub_insight(provider: str, category: str, symbol: str, msg: str) -> AIInsight:
    return AIInsight(
        provider=provider,
        category=category,
        symbol=symbol or None,
        summary=msg,
        data={"stub": True},
    )


# ===========================================================================
# Grok Fast Client  (24/7 raw data collector — news, social, filings)
# ===========================================================================
class GrokFastClient:
    """Grok 4.1 Fast: high-speed data collector & summariser."""

    MODEL = "grok-3-fast"  # xAI fast model identifier

    def __init__(self) -> None:
        self._client: Optional[AsyncOpenAI] = None
        if settings.XAI_GROK_API_KEY:
            self._client = AsyncOpenAI(
                api_key=settings.XAI_GROK_API_KEY,
                base_url="https://api.x.ai/v1",
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=30),
    )
    async def summarise_news(self, raw_text: str, symbol: str = "") -> AIInsight:
        """Summarise raw news text into structured JSON insight."""
        await broadcast_thought("grok_fast", "summarise_news", f"Summarising news for {symbol or 'market'}")

        if not await check_budget():
            return _stub_insight("grok_fast", "news", symbol, "Daily AI budget exceeded")

        if not self._client:
            return _stub_insight("grok_fast", "news", symbol, "Grok Fast client not configured (no API key)")

        safe_text = sanitize_user_input(raw_text[:8000])
        prompt = (
            f"You are a financial news analyst. Analyse the following news about {symbol or 'the market'}.\n"
            "Return a JSON object with keys: summary (str), sentiment_score (float -1 to 1), "
            "key_topics (list[str]), high_impact_keywords (list[str]).\n\n"
            f"News:\n{safe_text}"
        )

        resp = await self._client.chat.completions.create(
            model=self.MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.choices[0].message.content or ""
        usage_data = resp.usage
        usage = {
            "input": usage_data.prompt_tokens if usage_data else 0,
            "output": usage_data.completion_tokens if usage_data else 0,
        }
        cost = _estimate_cost("grok-fast", usage["input"], usage["output"])
        await _log_usage("grok_fast", self.MODEL, usage["input"], usage["output"], cost, "summarise_news")

        parsed = _safe_parse(text)
        sentiment = SentimentResult(
            source="news",
            summary=parsed.get("summary", text[:500]),
            sentiment_score=float(parsed.get("sentiment_score", 0)),
            key_topics=parsed.get("key_topics", []),
            high_impact_keywords=parsed.get("high_impact_keywords", []),
            reliability_score=0.9,  # news sources are generally reliable
        )
        return AIInsight(
            provider="grok_fast",
            category="news",
            symbol=symbol or None,
            summary=sentiment.summary,
            data=parsed,
            sentiment=sentiment,
            token_usage=usage,
            cost_usd=cost,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=30),
    )
    async def analyse_technical(self, market_data: dict, symbol: str) -> AIInsight:
        """Quick technical analysis of candle data."""
        await broadcast_thought("grok_fast", "analyse_technical", f"Technical analysis for {symbol}")

        if not await check_budget():
            return _stub_insight("grok_fast", "technical", symbol, "Daily AI budget exceeded")

        if not self._client:
            return _stub_insight("grok_fast", "technical", symbol, "Grok Fast client not configured")

        prompt = (
            f"You are a technical analyst. Given the following OHLCV data for {symbol}, "
            "provide a brief technical analysis.\n"
            "Return JSON: {analysis: str, signal: 'bullish'|'bearish'|'neutral', "
            "support: float, resistance: float}\n\n"
            f"Data:\n{json.dumps(market_data)[:6000]}"
        )

        resp = await self._client.chat.completions.create(
            model=self.MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.choices[0].message.content or ""
        usage_data = resp.usage
        usage = {
            "input": usage_data.prompt_tokens if usage_data else 0,
            "output": usage_data.completion_tokens if usage_data else 0,
        }
        cost = _estimate_cost("grok-fast", usage["input"], usage["output"])
        await _log_usage("grok_fast", self.MODEL, usage["input"], usage["output"], cost, "analyse_technical")

        parsed = _safe_parse(text)
        return AIInsight(
            provider="grok_fast",
            category="technical",
            symbol=symbol,
            summary=parsed.get("analysis", text[:500]),
            data=parsed,
            token_usage=usage,
            cost_usd=cost,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=30),
    )
    async def analyse_social(self, raw_posts: str, symbol: str = "") -> AIInsight:
        """Analyse social-media posts with noise filtering."""
        await broadcast_thought("grok_fast", "analyse_social", f"Social sentiment for {symbol or 'market'}")

        if not await check_budget():
            return _stub_insight("grok_fast", "social", symbol, "Daily AI budget exceeded")

        if not self._client:
            return _stub_insight("grok_fast", "social", symbol, "Grok Fast client not configured (no API key)")

        safe_posts = sanitize_user_input(raw_posts[:8000])

        # If noise filter is enabled, ask Grok to also score source reliability
        noise_instructions = ""
        if settings.SOCIAL_NOISE_FILTER_ENABLED:
            noise_instructions = (
                "\nAlso evaluate each source's reliability. "
                "Return additional keys: reliability_score (float 0-1, "
                "where 1 = verified journalist/official, 0.2 = anonymous small account), "
                "noise_filtered (bool: true if the data is mostly noise/spam/bots), "
                "reliable_source_count (int), discarded_source_count (int).\n"
            )

        prompt = (
            f"Analyse the following social-media posts about {symbol or 'the stock market'}.\n"
            "Return JSON: {summary: str, sentiment_score: float(-1..1), "
            "key_topics: list[str], high_impact_keywords: list[str], fud_detected: bool"
            + (", reliability_score: float, noise_filtered: bool, "
               "reliable_source_count: int, discarded_source_count: int"
               if settings.SOCIAL_NOISE_FILTER_ENABLED else "")
            + "}\n"
            + noise_instructions
            + f"\nPosts:\n{safe_posts}"
        )

        resp = await self._client.chat.completions.create(
            model=self.MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.choices[0].message.content or ""
        usage_data = resp.usage
        usage = {
            "input": usage_data.prompt_tokens if usage_data else 0,
            "output": usage_data.completion_tokens if usage_data else 0,
        }
        cost = _estimate_cost("grok-fast", usage["input"], usage["output"])
        await _log_usage("grok_fast", self.MODEL, usage["input"], usage["output"], cost, "analyse_social")

        parsed = _safe_parse(text)
        reliability = float(parsed.get("reliability_score", 1.0))

        # Apply noise filter: downweight low-reliability sentiment
        raw_sentiment = float(parsed.get("sentiment_score", 0))
        if settings.SOCIAL_NOISE_FILTER_ENABLED and reliability < 0.5:
            adjusted_sentiment = raw_sentiment * settings.LOW_RELIABILITY_WEIGHT
        else:
            adjusted_sentiment = raw_sentiment

        sentiment = SentimentResult(
            source="twitter",
            summary=parsed.get("summary", text[:500]),
            sentiment_score=adjusted_sentiment,
            key_topics=parsed.get("key_topics", []),
            high_impact_keywords=parsed.get("high_impact_keywords", []),
            reliability_score=reliability,
        )
        return AIInsight(
            provider="grok_fast",
            category="social",
            symbol=symbol or None,
            summary=sentiment.summary,
            data=parsed,
            sentiment=sentiment,
            token_usage=usage,
            cost_usd=cost,
        )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=30),
    )
    async def select_universe(self, current_symbols: list[str], market_context: str = "") -> list[dict]:
        """Select daily dynamic ticker universe based on trending/earnings/sector rotation."""
        await broadcast_thought("grok_fast", "select_universe", "Selecting daily ticker universe")

        if not await check_budget():
            return []

        if not self._client:
            return []

        prompt = (
            f"You are a stock screener AI. Select the top {settings.DYNAMIC_UNIVERSE_SIZE} US stock tickers "
            "that are most interesting to trade today based on:\n"
            "- Trending on social media / news\n"
            "- Upcoming or recent earnings\n"
            "- Sector rotation signals\n"
            "- Unusual volume or momentum\n\n"
            "HARD FILTERS (exclude any ticker that fails these):\n"
            f"- Market cap must be >= ${settings.UNIVERSE_MIN_MARKET_CAP_USD:,.0f}\n"
            f"- Daily trading volume must be >= ${settings.UNIVERSE_MIN_VOLUME_USD:,.0f}\n\n"
            f"Current watchlist: {current_symbols}\n"
            f"Market context: {market_context[:2000]}\n\n"
            "Return JSON array: [{symbol: str, reason: str, sector: str, score: float(0-1), "
            "source: str}]"
        )

        resp = await self._client.chat.completions.create(
            model=self.MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.choices[0].message.content or ""
        usage_data = resp.usage
        usage = {
            "input": usage_data.prompt_tokens if usage_data else 0,
            "output": usage_data.completion_tokens if usage_data else 0,
        }
        cost = _estimate_cost("grok-fast", usage["input"], usage["output"])
        await _log_usage("grok_fast", self.MODEL, usage["input"], usage["output"], cost, "select_universe")

        parsed = _safe_parse(text)
        if isinstance(parsed, dict) and "raw" in parsed:
            # Try to extract JSON array
            match = re.search(r"\[.*\]", text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
            return []
        if isinstance(parsed, list):
            return parsed
        return parsed.get("candidates", parsed.get("tickers", []))


# ===========================================================================
# Grok Strategy Client  (Strategy brainstormer — generates hypotheses)
# ===========================================================================
class GrokStrategyClient:
    """Grok 4.2: deep strategy brainstormer that generates trade hypotheses."""

    MODEL = "grok-3"  # xAI model identifier

    def __init__(self) -> None:
        self._client: Optional[AsyncOpenAI] = None
        if settings.XAI_GROK_API_KEY:
            self._client = AsyncOpenAI(
                api_key=settings.XAI_GROK_API_KEY,
                base_url="https://api.x.ai/v1",
            )

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=2, max=30),
    )
    async def brainstorm_hypotheses(
        self,
        market_data: dict,
        insights: list[dict],
        portfolio: dict,
    ) -> list[StrategyHypothesis]:
        """
        Generate 1-5 trade hypotheses based on data collected by Grok Fast.
        These will be reviewed and approved/rejected by Opus CEO.
        """
        await broadcast_thought(
            "grok_strategy", "brainstorm",
            f"Brainstorming trade hypotheses from {len(insights)} insights",
        )

        if not await check_budget():
            return []

        if not self._client:
            return []

        prompt = (
            "You are a quantitative strategy brainstormer. "
            "Based on the market data and AI insights below, generate 1-5 concrete trade hypotheses.\n\n"
            "Each hypothesis should be specific: which stock, buy or sell, why, "
            "confidence level, and expected timeframe.\n\n"
            "=== MARKET DATA ===\n"
            f"{json.dumps(market_data, default=str)[:4000]}\n\n"
            "=== AI INSIGHTS ===\n"
            f"{json.dumps(insights, default=str)[:4000]}\n\n"
            "=== CURRENT PORTFOLIO ===\n"
            f"{json.dumps(portfolio, default=str)[:2000]}\n\n"
            "Return JSON array: [\n"
            '  {"hypothesis_id": "H001", "symbol": "AAPL", "direction": "buy"|"sell", '
            '"rationale": "...", "confidence": 0.0-1.0, "timeframe": "intraday"|"swing"|"position", '
            '"supporting_data": {...}}\n'
            "]\n"
            "Only include hypotheses you have genuine conviction about. Quality over quantity."
        )

        resp = await self._client.chat.completions.create(
            model=self.MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.choices[0].message.content or ""
        usage_data = resp.usage
        usage = {
            "input": usage_data.prompt_tokens if usage_data else 0,
            "output": usage_data.completion_tokens if usage_data else 0,
        }
        cost = _estimate_cost("grok-strategy", usage["input"], usage["output"])
        await _log_usage("grok_strategy", self.MODEL, usage["input"], usage["output"], cost, "brainstorm")

        parsed = _safe_parse(text)
        hypotheses = []

        items = parsed if isinstance(parsed, list) else parsed.get("hypotheses", parsed.get("raw", []))
        if isinstance(items, str):
            items = []

        for h in items:
            if not isinstance(h, dict):
                continue
            try:
                hypotheses.append(StrategyHypothesis(
                    hypothesis_id=h.get("hypothesis_id", f"H{len(hypotheses)+1:03d}"),
                    symbol=h.get("symbol", ""),
                    direction=h.get("direction", "buy"),
                    rationale=h.get("rationale", ""),
                    confidence=float(h.get("confidence", 0.5)),
                    timeframe=h.get("timeframe", "intraday"),
                    supporting_data=h.get("supporting_data", {}),
                ))
            except Exception:
                continue

        await broadcast_thought(
            "grok_strategy", "hypotheses_ready",
            f"Generated {len(hypotheses)} hypotheses for Opus CEO review",
            {"hypotheses": [h.model_dump(mode="json") for h in hypotheses]},
        )

        return hypotheses


# ===========================================================================
# Claude Opus Client  (CEO — final approver / decision maker)
# ===========================================================================
class OpusClient:
    """Claude Opus 4.6: CEO that reviews Grok hypotheses and makes final decisions."""

    MODEL = "claude-opus-4-20250514"

    def __init__(self) -> None:
        self._client: Optional[anthropic.AsyncAnthropic] = None
        if settings.ANTHROPIC_API_KEY:
            self._client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    # ------------------------------------------------------------------
    # Tool definitions that Opus can call via tool_use
    # ------------------------------------------------------------------
    TOOLS = [
        {
            "name": "get_ai_insights",
            "description": (
                "Invoke Grok Fast to fetch fresh insights. "
                "task is 'news', 'technical', or 'social'."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "task": {"type": "string", "enum": ["news", "technical", "social"]},
                    "symbol": {"type": "string", "description": "Stock ticker"},
                    "raw_data": {"type": "string", "description": "Raw text to analyse"},
                },
                "required": ["task"],
            },
        },
        {
            "name": "perform_trade",
            "description": (
                "Execute a trade order. Side is 'buy' or 'sell'. "
                "qty is number of shares. order_type is 'market' or 'limit'."
            ),
            "input_schema": {
                "type": "object",
                "properties": {
                    "symbol": {"type": "string"},
                    "side": {"type": "string", "enum": ["buy", "sell"]},
                    "qty": {"type": "number"},
                    "order_type": {"type": "string", "enum": ["market", "limit"], "default": "market"},
                    "limit_price": {"type": "number", "description": "Required if order_type is limit"},
                    "reason": {"type": "string"},
                    "confidence": {"type": "number", "description": "Confidence 0-1"},
                },
                "required": ["symbol", "side", "qty"],
            },
        },
        {
            "name": "get_portfolio_status",
            "description": "Retrieve current portfolio: equity, cash, positions, P&L.",
            "input_schema": {
                "type": "object",
                "properties": {},
            },
        },
    ]

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(min=5, max=60),
        retry=retry_if_exception_type((anthropic.APITimeoutError, anthropic.APIConnectionError)),
    )
    async def plan_strategy(
        self,
        market_data: dict,
        insights: list[dict],
        portfolio: dict,
        trade_history: list[dict],
        previous_reviews: list[dict],
        hypotheses: list[dict] | None = None,
        tool_executor=None,
    ) -> StrategyDecision:
        """
        CEO strategy cycle: review Grok's hypotheses, approve/reject, execute trades.
        Opus may call tools autonomously via tool_use.
        """
        await broadcast_thought(
            "opus", "plan_strategy",
            "CEO starting strategy review — evaluating Grok hypotheses",
            {"n_insights": len(insights), "n_hypotheses": len(hypotheses or [])},
        )

        if not await check_budget():
            return StrategyDecision(
                reasoning="Daily AI budget exceeded. No new strategy decisions until budget resets.",
                actions=[],
                self_correction="Budget limit reached — holding all positions.",
            )

        if not self._client:
            return StrategyDecision(
                reasoning="Opus client not configured (no API key). Returning no-op strategy.",
                actions=[],
                self_correction="N/A — stub mode",
            )

        system_prompt = self._build_system_prompt()
        user_message = self._build_user_message(
            market_data, insights, portfolio, trade_history, previous_reviews, hypotheses
        )

        messages = [{"role": "user", "content": user_message}]

        # Agentic loop: keep going until Opus produces a final text answer
        total_input = 0
        total_output = 0
        max_turns = 10

        for turn in range(max_turns):
            resp = await self._client.messages.create(
                model=self.MODEL,
                max_tokens=4096,
                system=system_prompt,
                tools=self.TOOLS,
                messages=messages,
            )

            total_input += resp.usage.input_tokens
            total_output += resp.usage.output_tokens

            # Check if Opus wants to use tools
            if resp.stop_reason == "tool_use":
                # Process each tool-use block
                tool_results = []
                for block in resp.content:
                    if block.type == "tool_use":
                        await broadcast_thought(
                            "opus", "tool_call",
                            f"CEO calling tool: {block.name}",
                            {"args": block.input},
                        )
                        result = await self._execute_tool(
                            block.name, block.input, block.id, tool_executor
                        )
                        tool_results.append(result)

                # Feed results back to Opus
                messages.append({"role": "assistant", "content": resp.content})
                messages.append({
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": r["tool_use_id"],
                            "content": json.dumps(r["content"], default=str),
                        }
                        for r in tool_results
                    ],
                })
                continue

            # Final text response from Opus
            final_text = ""
            for block in resp.content:
                if hasattr(block, "text"):
                    final_text += block.text

            break
        else:
            final_text = "Max tool-use turns reached. Strategy cycle incomplete."

        cost = _estimate_cost("claude-opus", total_input, total_output)
        await _log_usage("opus", self.MODEL, total_input, total_output, cost, "plan_strategy")

        await broadcast_thought(
            "opus", "strategy_complete",
            "CEO strategy review finished",
            {"total_input_tokens": total_input, "total_output_tokens": total_output, "cost_usd": cost},
        )

        return self._parse_strategy(final_text, total_input, total_output, cost)

    async def chat(self, message: str, context_data: dict | None = None) -> dict:
        """Handle a user chat message about strategy/risk."""
        if not self._client:
            return {"reply": "Opus client not configured.", "cost_usd": 0.0}

        if not await check_budget():
            return {"reply": "Daily AI budget exceeded. Please try again tomorrow.", "cost_usd": 0.0}

        safe_msg = sanitize_user_input(message)
        system = (
            "You are the CEO AI of Project Scolecite, an autonomous trading system. "
            "Answer the user's question about trading strategy, risk management, or market analysis. "
            "Be concise, professional, and data-driven. "
            "You have access to portfolio and market context provided below."
        )
        user_content = safe_msg
        if context_data:
            user_content += f"\n\n[Current context: {json.dumps(context_data, default=str)[:3000]}]"

        resp = await self._client.messages.create(
            model=self.MODEL,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user_content}],
        )
        text = resp.content[0].text if resp.content else ""
        usage = {"input": resp.usage.input_tokens, "output": resp.usage.output_tokens}
        cost = _estimate_cost("claude-opus", usage["input"], usage["output"])
        await _log_usage("opus", self.MODEL, usage["input"], usage["output"], cost, "chat")

        return {
            "reply": text,
            "token_usage": usage,
            "cost_usd": cost,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _build_system_prompt(self) -> str:
        return (
            "You are Claude Opus 4.6, the CEO of Project Scolecite, "
            "an autonomous AI trading system for the US stock market.\n\n"
            "YOUR ROLE: Final decision-maker. Grok 4.2 has generated trade hypotheses "
            "for you to review. You APPROVE or REJECT each hypothesis based on:\n"
            "- Real portfolio data and risk constraints\n"
            "- Quality and reliability of the supporting data\n"
            "- Current market conditions and sentiment\n"
            "- Previous trade outcomes and self-corrections\n\n"
            "YOUR CAPABILITIES:\n"
            "- Call get_ai_insights to request fresh data from Grok Fast.\n"
            "- Call get_portfolio_status to check current positions.\n"
            "- Call perform_trade to execute approved trades.\n\n"
            "YOUR MANDATE:\n"
            "1. Review each hypothesis from Grok 4.2.\n"
            "2. For each, clearly state APPROVED or REJECTED with reasoning.\n"
            "3. For approved hypotheses, execute trades via perform_trade.\n"
            "4. Review previous trade outcomes and write self-correction notes.\n"
            "5. Respond with a JSON block:\n"
            "```json\n"
            '{"reasoning": "...", "risk_notes": "...", "confidence": 0.0-1.0, '
            '"self_correction": "...", "hypothesis_accepted": ["H001"], '
            '"hypothesis_rejected": ["H002"], "actions_taken": [...]}\n'
            "```\n\n"
            f"CONSTRAINTS: TRADING_MODE={settings.TRADING_MODE}, "
            f"MAX_POSITION={settings.MAX_POSITION_PERCENT}%, "
            f"MAX_DRAWDOWN={settings.MAX_DRAWDOWN_PERCENT}%, "
            f"DAILY_LOSS_LIMIT={settings.DAILY_LOSS_LIMIT_PERCENT}%.\n"
            f"ADAPTIVE_STOPLOSS={'ENABLED' if settings.ENABLE_ADAPTIVE_STOPLOSS else 'DISABLED'}"
            f"{f', HARD_CAP={settings.ADAPTIVE_STOPLOSS_HARD_CAP_PCT}%' if settings.ENABLE_ADAPTIVE_STOPLOSS else ''}.\n"
            "If confidence is below 0.6, strongly prefer holding. "
            "Always justify every trade with clear reasoning."
            + (
                "\n\nSTOP-LOSS RULE: When Adaptive Stop-Loss is ENABLED, "
                "analyse each position's ATR (Average True Range) to set a dynamic stop-loss. "
                f"The stop-loss distance MUST NEVER exceed {settings.ADAPTIVE_STOPLOSS_HARD_CAP_PCT}% "
                "from entry price (hard cap)."
                if settings.ENABLE_ADAPTIVE_STOPLOSS else ""
            )
        )

    @staticmethod
    def _build_user_message(
        market_data: dict,
        insights: list[dict],
        portfolio: dict,
        trade_history: list[dict],
        previous_reviews: list[dict],
        hypotheses: list[dict] | None = None,
    ) -> str:
        parts = [
            "=== MARKET DATA ===",
            json.dumps(market_data, default=str)[:4000],
            "",
            "=== AI INSIGHTS (Grok Fast) ===",
            json.dumps(insights, default=str)[:4000],
            "",
            "=== CURRENT PORTFOLIO ===",
            json.dumps(portfolio, default=str)[:2000],
            "",
            "=== RECENT TRADE HISTORY ===",
            json.dumps(trade_history, default=str)[:2000],
            "",
            "=== PREVIOUS SELF-CORRECTIONS ===",
            json.dumps(previous_reviews, default=str)[:2000],
        ]
        if hypotheses:
            parts.extend([
                "",
                "=== GROK 4.2 HYPOTHESES (for your review) ===",
                json.dumps(hypotheses, default=str)[:3000],
            ])
        parts.extend([
            "",
            "Review the above data and Grok's hypotheses. "
            "APPROVE or REJECT each hypothesis. Execute trades for approved ones. "
            "Finish with your JSON strategy summary.",
        ])
        return "\n".join(parts)

    async def _execute_tool(
        self, name: str, args: dict, tool_use_id: str, executor
    ) -> dict:
        """Dispatch tool call to the executor (MCP server layer)."""
        try:
            if executor:
                result = await executor(name, args)
            else:
                result = {"error": "No tool executor configured"}
            return {"tool_use_id": tool_use_id, "content": result}
        except Exception as e:
            logger.error("tool_execution_failed", tool=name, error=str(e))
            return {"tool_use_id": tool_use_id, "content": {"error": str(e)}}

    def _parse_strategy(
        self, text: str, inp: int, out: int, cost: float
    ) -> StrategyDecision:
        parsed = _safe_parse(text)
        if "raw" in parsed:
            # Try to extract JSON block from mixed text
            match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group(1))
                except json.JSONDecodeError:
                    pass

        actions = []
        for a in parsed.get("actions_taken", parsed.get("actions", [])):
            try:
                actions.append(TradeRequest(**a))
            except Exception:
                pass

        return StrategyDecision(
            reasoning=parsed.get("reasoning", text[:1000]),
            actions=actions,
            risk_notes=parsed.get("risk_notes", ""),
            confidence=float(parsed.get("confidence", 0.5)),
            self_correction=parsed.get("self_correction", ""),
            hypothesis_accepted=parsed.get("hypothesis_accepted", []),
            hypothesis_rejected=parsed.get("hypothesis_rejected", []),
            token_usage={"input": inp, "output": out},
            cost_usd=cost,
        )
