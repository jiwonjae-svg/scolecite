# =============================================================================
# Project Scolecite - Orchestrator (AI Loop Controller)
# DISCLAIMER: For educational/research purposes only.
# The authors are NOT responsible for any financial losses.
# =============================================================================
"""
The Orchestrator runs the infinite AI loop with a 3-model architecture:
  1. Data Collection (Grok Fast)     — every GROK_SCAN_INTERVAL_MIN
  2. Hypothesis Generation (Grok Strategy) + CEO Review (Opus)
  3. Execution                       — triggered by Opus CEO decisions
  4. Self-Correction / Review        — after each strategy cycle
  5. Dynamic Universe Refresh        — periodic ticker selection
  6. Auto Journal & DB Backup        — nightly housekeeping

A lightweight watchdog loop (1-min interval) checks for urgency conditions
(price spikes, sentiment drops, drawdowns) and triggers an immediate
strategy cycle via asyncio.Event.

All loops run concurrently via asyncio.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta
from typing import Any, Optional

from sqlalchemy import select, func

from shared.config import get_settings
from shared.schemas import (
    AIInsight,
    BotStatus,
    JournalEntry,
    PortfolioStatus,
    ReviewEntry,
    StrategyDecision,
    StrategyHypothesis,
    ThoughtLog,
    TickerCard,
)
from server.core.ai_clients import (
    GrokFastClient,
    GrokStrategyClient,
    OpusClient,
    check_budget,
    get_cost_summary,
)
from server.core.mcp_server import MCPServer
from server.core.trading_engine import TradingEngine
from server.core.risk_manager import RiskManager
from server.database import (
    async_session_factory,
    backup_sqlite_db,
    JournalRecord,
    ThoughtRecord,
    ReviewRecord,
    StrategyRecord,
)
from server.utils.logging import broadcast_thought, get_logger

logger = get_logger("orchestrator")
settings = get_settings()


class Orchestrator:
    """
    Central controller that manages all AI loops.
    Designed to be run inside an asyncio event loop alongside FastAPI.
    """

    def __init__(self) -> None:
        # Components — 3-model architecture
        self.risk_manager = RiskManager()
        self.grok_fast = GrokFastClient()
        self.grok_strategy = GrokStrategyClient()
        self.opus = OpusClient()
        self.engine = TradingEngine(self.risk_manager)
        self.mcp = MCPServer(self.grok_fast, self.engine, self.risk_manager)

        # State
        self.status: BotStatus = BotStatus.STOPPED
        self.strategy_version: int = 0
        self.current_strategy: Optional[StrategyDecision] = None

        # Dynamic ticker universe (updated by Grok Fast)
        self.tracked_symbols: list[str] = ["AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"]
        self._default_symbols: list[str] = list(self.tracked_symbols)

        # Latest collected data (shared with SSE subscribers)
        self.latest_insights: list[AIInsight] = []
        self.latest_portfolio: Optional[PortfolioStatus] = None
        self.latest_market: dict[str, dict] = {}
        self.latest_hypotheses: list[StrategyHypothesis] = []
        self.ticker_cards: list[TickerCard] = []

        # SSE event queue for broadcasting to clients
        self._sse_queues: list[asyncio.Queue] = []

        # Control
        self._tasks: list[asyncio.Task] = []
        self._last_universe_refresh: Optional[datetime] = None
        self._last_backup: Optional[datetime] = None
        self._last_journal: Optional[datetime] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Start all loops."""
        if self.status == BotStatus.RUNNING:
            return

        await self.engine.start()
        self.status = BotStatus.RUNNING

        await broadcast_thought("orchestrator", "start", "Bot started — launching AI loops")

        self._tasks = [
            asyncio.create_task(self._data_collection_loop(), name="data_collection"),
            asyncio.create_task(self._strategy_loop(), name="strategy"),
            asyncio.create_task(self._watchdog_loop(), name="watchdog"),
            asyncio.create_task(self._housekeeping_loop(), name="housekeeping"),
        ]

    async def stop(self) -> None:
        """Gracefully stop all loops."""
        self.status = BotStatus.STOPPED
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        await self.engine.stop()
        await broadcast_thought("orchestrator", "stop", "Bot stopped")

    async def emergency_stop(self) -> list[dict]:
        """Kill switch: liquidate everything and halt."""
        self.risk_manager.activate_emergency_stop()
        self.status = BotStatus.EMERGENCY_STOP
        results = await self.engine.liquidate_all()
        await self.stop()
        await broadcast_thought(
            "orchestrator", "emergency_stop",
            f"EMERGENCY STOP — liquidated {len(results)} positions",
        )
        return [r.model_dump(mode="json") for r in results]

    # ------------------------------------------------------------------
    # SSE subscriber management
    # ------------------------------------------------------------------
    def subscribe_sse(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._sse_queues.append(q)
        return q

    def unsubscribe_sse(self, q: asyncio.Queue) -> None:
        if q in self._sse_queues:
            self._sse_queues.remove(q)

    async def _push_sse(self, event: str, data: dict) -> None:
        dead: list[asyncio.Queue] = []
        for q in self._sse_queues:
            try:
                q.put_nowait({"event": event, "data": data})
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._sse_queues.remove(q)

    # ------------------------------------------------------------------
    # Loop 1: Data Collection (Grok Fast)
    # ------------------------------------------------------------------
    async def _data_collection_loop(self) -> None:
        """Runs every GROK_SCAN_INTERVAL_MIN minutes."""
        while self.status == BotStatus.RUNNING:
            try:
                # Check rest mode — skip collection if resting
                if self.risk_manager.check_rest_mode():
                    self.status = BotStatus.REST_MODE
                    await broadcast_thought(
                        "orchestrator", "rest_mode",
                        "Bot is in rest mode — skipping data collection",
                    )
                    await asyncio.sleep(settings.GROK_SCAN_INTERVAL_MIN * 60)
                    continue

                if self.status == BotStatus.REST_MODE:
                    self.status = BotStatus.RUNNING

                # Check budget
                if not check_budget():
                    await broadcast_thought(
                        "orchestrator", "budget_exceeded",
                        "Daily AI budget exceeded — skipping data collection",
                    )
                    await asyncio.sleep(settings.GROK_SCAN_INTERVAL_MIN * 60)
                    continue

                await broadcast_thought(
                    "orchestrator", "data_collection_start",
                    f"Starting data collection for {len(self.tracked_symbols)} symbols",
                )

                insights: list[AIInsight] = []

                # Fetch market snapshots in parallel
                snapshot_tasks = [
                    self.engine.get_snapshot(sym) for sym in self.tracked_symbols
                ]
                snapshots = await asyncio.gather(*snapshot_tasks, return_exceptions=True)

                for sym, snap in zip(self.tracked_symbols, snapshots):
                    if isinstance(snap, Exception):
                        logger.warning("snapshot_error", symbol=sym, error=str(snap))
                        continue
                    snap_dict = snap.model_dump(mode="json")
                    self.latest_market[sym] = snap_dict
                    self.mcp.update_market_cache(sym, snap_dict)
                    await self._push_sse("market_data", {"symbol": sym, **snap_dict})

                # Grok Fast technical analysis (parallel)
                tech_tasks = [
                    self.grok_fast.analyse_technical(
                        self.latest_market.get(sym, {}), sym
                    )
                    for sym in self.tracked_symbols
                ]
                tech_results = await asyncio.gather(*tech_tasks, return_exceptions=True)
                for r in tech_results:
                    if isinstance(r, AIInsight):
                        insights.append(r)

                # Grok Fast social sentiment (sequential for rate limits)
                for sym in self.tracked_symbols[:5]:
                    try:
                        social = await self.grok_fast.analyse_social(
                            f"Latest social media discussion about ${sym} stock",
                            sym,
                        )
                        insights.append(social)
                    except Exception as e:
                        logger.warning("grok_social_error", symbol=sym, error=str(e))

                self.latest_insights = insights

                # Build ticker cards
                self._build_ticker_cards()

                # Push insights via SSE
                for ins in insights:
                    await self._push_sse("insight", ins.model_dump(mode="json"))

                # Save thought logs to DB
                await self._save_thought("orchestrator", "data_collected",
                    f"Collected {len(insights)} insights",
                    {"symbols": self.tracked_symbols, "count": len(insights)})

                # Update portfolio
                self.latest_portfolio = await self.engine.get_portfolio()
                await self._push_sse("portfolio", self.latest_portfolio.model_dump(mode="json"))

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("data_collection_error", error=str(e))

            await asyncio.sleep(settings.GROK_SCAN_INTERVAL_MIN * 60)

    # ------------------------------------------------------------------
    # Loop 2: Strategy (Grok Strategy → Opus CEO)
    # ------------------------------------------------------------------
    async def _strategy_loop(self) -> None:
        """
        Runs every STRATEGY_UPDATE_INTERVAL_MIN minutes,
        OR immediately when the watchdog triggers urgent_event.
        """
        while self.status == BotStatus.RUNNING:
            try:
                # Wait for either the scheduled interval or an urgency trigger
                try:
                    await asyncio.wait_for(
                        self.risk_manager.urgent_event.wait(),
                        timeout=settings.STRATEGY_UPDATE_INTERVAL_MIN * 60,
                    )
                    self.risk_manager.urgent_event.clear()
                    await broadcast_thought(
                        "orchestrator", "urgent_strategy",
                        "Urgency detected — running immediate strategy cycle",
                    )
                except asyncio.TimeoutError:
                    pass

                if self.status not in (BotStatus.RUNNING,):
                    if self.status == BotStatus.REST_MODE:
                        continue
                    break

                # Check budget before expensive Opus call
                if not check_budget():
                    await broadcast_thought(
                        "orchestrator", "budget_exceeded",
                        "Daily AI budget exceeded — skipping strategy cycle",
                    )
                    continue

                await self._run_strategy_cycle()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("strategy_loop_error", error=str(e))
                await asyncio.sleep(60)

    async def _run_strategy_cycle(self) -> None:
        """
        Execute one full strategy cycle:
        1. Grok Strategy brainstorms hypotheses
        2. Opus CEO reviews & approves/rejects
        """
        await broadcast_thought(
            "orchestrator", "strategy_cycle_start",
            f"Starting strategy cycle (version {self.strategy_version + 1})",
        )

        # Gather context
        portfolio = self.latest_portfolio or await self.engine.get_portfolio()
        trade_history = await self.engine.get_trade_history(limit=20)
        reviews = await self._get_recent_reviews()
        insights_data = [i.model_dump(mode="json") for i in self.latest_insights]

        # Step 1: Grok Strategy brainstorms hypotheses
        try:
            hypotheses = await self.grok_strategy.brainstorm_hypotheses(
                market_data=self.latest_market,
                insights=insights_data,
                portfolio=portfolio.model_dump(mode="json"),
            )
            self.latest_hypotheses = hypotheses
            await broadcast_thought(
                "orchestrator", "hypotheses_generated",
                f"Grok Strategy generated {len(hypotheses)} hypotheses",
                {"hypotheses": [h.model_dump(mode="json") for h in hypotheses]},
            )
        except Exception as e:
            logger.error("hypothesis_generation_failed", error=str(e))
            hypotheses = []

        # Step 2: Opus CEO reviews hypotheses & makes final decisions
        decision = await self.opus.plan_strategy(
            market_data=self.latest_market,
            insights=insights_data,
            portfolio=portfolio.model_dump(mode="json"),
            trade_history=trade_history,
            previous_reviews=reviews,
            tool_executor=self.mcp.call_tool,
            hypotheses=[h.model_dump(mode="json") for h in hypotheses],
        )

        self.strategy_version += 1
        decision.version = self.strategy_version
        self.current_strategy = decision

        # Track trade outcomes for fatigue detection
        for action in decision.actions:
            # Record any executed actions as pending outcomes
            pass

        # Save strategy
        await self._save_strategy(decision)

        # Push via SSE
        await self._push_sse("strategy", {
            "version": decision.version,
            "reasoning": decision.reasoning,
            "confidence": decision.confidence,
            "self_correction": decision.self_correction,
            "risk_notes": decision.risk_notes,
            "actions_count": len(decision.actions),
            "cost_usd": decision.cost_usd,
            "hypothesis_accepted": decision.hypothesis_accepted,
            "hypothesis_rejected": decision.hypothesis_rejected,
        })

        # Save self-correction as review
        if decision.self_correction:
            await self._save_review(decision)
            await self._push_sse("review", {
                "strategy_version": decision.version,
                "self_correction": decision.self_correction,
            })

        await broadcast_thought(
            "orchestrator", "strategy_cycle_complete",
            f"Strategy v{decision.version} complete "
            f"(confidence={decision.confidence:.2f}, "
            f"accepted={decision.hypothesis_accepted}, "
            f"rejected={decision.hypothesis_rejected})",
            {"reasoning_preview": decision.reasoning[:200]},
        )

    # ------------------------------------------------------------------
    # Loop 3: Watchdog (1-min urgency checks)
    # ------------------------------------------------------------------
    async def _watchdog_loop(self) -> None:
        """Checks volatility / sentiment every minute."""
        while self.status in (BotStatus.RUNNING, BotStatus.REST_MODE):
            try:
                if not self.latest_portfolio or not self.latest_market:
                    await asyncio.sleep(60)
                    continue

                for sym in self.tracked_symbols:
                    snap = self.latest_market.get(sym, {})
                    price_change = snap.get("change_pct", 0.0)
                    volume = snap.get("volume", 0)
                    avg_volume = snap.get("avg_volume", 1)
                    volume_ratio = volume / avg_volume if avg_volume > 0 else 0

                    # Latest sentiment for this symbol
                    sent_score = None
                    for ins in reversed(self.latest_insights):
                        if ins.sentiment and ins.symbol == sym:
                            sent_score = ins.sentiment.sentiment_score
                            break

                    # Individual drawdowns
                    drawdowns: dict[str, float] = {}
                    for pos in self.latest_portfolio.positions:
                        drawdowns[pos.symbol] = pos.unrealized_pl_pct

                    # Sector ETF proxy (QQQ if tracked)
                    sector_change = self.latest_market.get("QQQ", {}).get("change_pct", 0.0)

                    await self.risk_manager.check_urgency(
                        price_change_5m_pct=price_change,
                        volume_vs_avg=volume_ratio,
                        current_sentiment=sent_score,
                        individual_drawdowns=drawdowns,
                        sector_etf_change_pct=sector_change,
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("watchdog_error", error=str(e))

            await asyncio.sleep(60)

    # ------------------------------------------------------------------
    # Loop 4: Housekeeping (universe refresh, journal, backup)
    # ------------------------------------------------------------------
    async def _housekeeping_loop(self) -> None:
        """Periodic housekeeping tasks."""
        while self.status in (BotStatus.RUNNING, BotStatus.REST_MODE):
            try:
                now = datetime.utcnow()

                # Dynamic universe refresh (every 4 hours)
                if settings.ENABLE_DYNAMIC_UNIVERSE and (
                    not self._last_universe_refresh
                    or (now - self._last_universe_refresh) > timedelta(hours=4)
                ):
                    await self._refresh_universe()
                    self._last_universe_refresh = now

                # Auto trade journal (daily at ~midnight UTC)
                if (
                    not self._last_journal
                    or self._last_journal.date() < now.date()
                ):
                    await self._generate_journal()
                    self._last_journal = now

                # DB backup (daily)
                if settings.DB_BACKUP_ENABLED and (
                    not self._last_backup
                    or self._last_backup.date() < now.date()
                ):
                    backup_sqlite_db()
                    self._last_backup = now
                    await broadcast_thought(
                        "orchestrator", "db_backup", "Daily database backup completed",
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("housekeeping_error", error=str(e))

            await asyncio.sleep(300)  # check every 5 minutes

    # ------------------------------------------------------------------
    # Dynamic Universe
    # ------------------------------------------------------------------
    async def _refresh_universe(self) -> None:
        """Use Grok Fast to select the best tickers for the current market."""
        try:
            if not check_budget():
                return

            await broadcast_thought(
                "orchestrator", "universe_refresh",
                "Refreshing dynamic ticker universe...",
            )

            candidates = await self.grok_fast.select_universe(
                current_universe=self.tracked_symbols,
                market_summary=self.latest_market,
                max_size=settings.DYNAMIC_UNIVERSE_SIZE,
            )

            if candidates and candidates.tickers:
                new_symbols = [c.symbol for c in candidates.tickers]
                # Always keep default symbols
                merged = list(dict.fromkeys(self._default_symbols + new_symbols))
                self.tracked_symbols = merged[:settings.DYNAMIC_UNIVERSE_SIZE]
                self.mcp.update_universe(self.tracked_symbols)

                await broadcast_thought(
                    "orchestrator", "universe_updated",
                    f"Universe updated: {self.tracked_symbols}",
                    {"candidates": [c.model_dump(mode="json") for c in candidates.tickers]},
                )
                await self._push_sse("universe", {"symbols": self.tracked_symbols})

        except Exception as e:
            logger.warning("universe_refresh_failed", error=str(e))

    # ------------------------------------------------------------------
    # Auto Trade Journal
    # ------------------------------------------------------------------
    async def _generate_journal(self) -> None:
        """Generate a daily trade journal entry."""
        try:
            trade_history = await self.engine.get_trade_history(limit=50)
            today_trades = [
                t for t in trade_history
                if t.get("created_at", "").startswith(
                    datetime.utcnow().strftime("%Y-%m-%d")
                )
            ]

            if not today_trades:
                return

            portfolio = self.latest_portfolio or await self.engine.get_portfolio()

            # Build journal entry
            wins = sum(1 for t in today_trades if t.get("status") == "filled")
            losses = sum(1 for t in today_trades if t.get("status") == "error")

            entry = JournalEntry(
                date=datetime.utcnow().strftime("%Y-%m-%d"),
                summary=f"Executed {len(today_trades)} trades. "
                        f"Portfolio equity: ${portfolio.equity:,.2f}",
                trades_count=len(today_trades),
                wins=wins,
                losses=losses,
                pnl=portfolio.daily_pl,
                lessons=["Auto-generated journal — review needed"],
                market_conditions=f"Tracked {len(self.tracked_symbols)} symbols",
            )

            # Save to DB
            async with async_session_factory() as session:
                record = JournalRecord(
                    date=entry.date,
                    summary=entry.summary,
                    trades_count=entry.trades_count,
                    wins=entry.wins,
                    losses=entry.losses,
                    pnl=entry.pnl,
                    market_conditions=entry.market_conditions,
                )
                record.set_lessons(entry.lessons)
                session.add(record)
                await session.commit()

            await broadcast_thought(
                "orchestrator", "journal_generated",
                f"Daily journal: {entry.summary}",
            )

        except Exception as e:
            logger.warning("journal_generation_failed", error=str(e))

    # ------------------------------------------------------------------
    # Ticker Cards (for UI)
    # ------------------------------------------------------------------
    def _build_ticker_cards(self) -> None:
        """Build ticker card summaries from latest market data and insights."""
        cards = []
        for sym in self.tracked_symbols:
            snap = self.latest_market.get(sym, {})
            if not snap:
                continue

            # Find latest insight for this symbol
            signal = "NEUTRAL"
            confidence = 0.5
            for ins in reversed(self.latest_insights):
                if ins.symbol == sym:
                    if ins.sentiment:
                        score = ins.sentiment.sentiment_score
                        if score > 0.6:
                            signal = "BULLISH"
                        elif score < -0.3:
                            signal = "BEARISH"
                        confidence = abs(score)
                    break

            cards.append(TickerCard(
                symbol=sym,
                price=snap.get("price", 0.0),
                change_pct=snap.get("change_pct", 0.0),
                signal=signal,
                confidence=confidence,
                volume=snap.get("volume", 0),
            ))

        self.ticker_cards = cards

    # ------------------------------------------------------------------
    # Chat (pass-through to Opus)
    # ------------------------------------------------------------------
    async def chat(self, user_message: str) -> str:
        """Send a user message to Opus CEO and get a response."""
        context = {
            "portfolio": (self.latest_portfolio.model_dump(mode="json")
                          if self.latest_portfolio else {}),
            "market": {k: {"price": v.get("price"), "change_pct": v.get("change_pct")}
                       for k, v in self.latest_market.items()},
            "status": self.status.value,
            "strategy_version": self.strategy_version,
            "risk_status": self.risk_manager.get_status(),
            "ai_cost": get_cost_summary().model_dump(mode="json"),
        }
        return await self.opus.chat(user_message, context)

    # ------------------------------------------------------------------
    # DB Persistence helpers
    # ------------------------------------------------------------------
    async def _save_thought(
        self, agent: str, action: str, thought: str, data: dict | None = None
    ) -> None:
        try:
            async with async_session_factory() as session:
                record = ThoughtRecord(agent=agent, action=action, thought=thought)
                if data:
                    record.set_data(data)
                session.add(record)
                await session.commit()
        except Exception as e:
            logger.warning("save_thought_failed", error=str(e))

    async def _save_strategy(self, decision: StrategyDecision) -> None:
        try:
            async with async_session_factory() as session:
                record = StrategyRecord(
                    version=decision.version,
                    reasoning=decision.reasoning,
                    actions_json=json.dumps(
                        [a.model_dump(mode="json") for a in decision.actions],
                        default=str,
                    ),
                    risk_notes=decision.risk_notes,
                    confidence=decision.confidence,
                    self_correction=decision.self_correction,
                    token_usage_json=json.dumps(decision.token_usage),
                    cost_usd=decision.cost_usd,
                )
                session.add(record)
                await session.commit()
        except Exception as e:
            logger.warning("save_strategy_failed", error=str(e))

    async def _save_review(self, decision: StrategyDecision) -> None:
        try:
            async with async_session_factory() as session:
                record = ReviewRecord(
                    trade_id=f"strategy-v{decision.version}",
                    symbol="PORTFOLIO",
                    expected_outcome="Improvement over previous strategy",
                    actual_outcome="Pending evaluation",
                    error_analysis=decision.self_correction,
                    improvement=decision.risk_notes,
                    strategy_version=decision.version,
                )
                session.add(record)
                await session.commit()
        except Exception as e:
            logger.warning("save_review_failed", error=str(e))

    async def _get_recent_reviews(self, limit: int = 10) -> list[dict]:
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
                        "trade_id": r.trade_id,
                        "symbol": r.symbol,
                        "error_analysis": r.error_analysis,
                        "improvement": r.improvement,
                        "strategy_version": r.strategy_version,
                    }
                    for r in records
                ]
        except Exception:
            return []
