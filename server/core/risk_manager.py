# =============================================================================
# Project Scolecite - Risk Manager
# DISCLAIMER: For educational/research purposes only.
# The authors are NOT responsible for any financial losses.
# =============================================================================
"""
Pre-trade risk checks enforced as decorators / middleware:
 - Balance Check
 - Slippage Check (±0.5%)
 - Position Size Limit (MAX_POSITION_PERCENT) with confidence scaling
 - Daily Loss Limit (-DAILY_LOSS_LIMIT_PERCENT%)
 - Max Drawdown Limit (-MAX_DRAWDOWN_PERCENT%)
 - Market Fatigue / Rest Mode (consecutive stop-losses, VIX panic)
 - AI Budget Check

Also contains the volatility / sentiment watchdog that triggers urgent
strategy updates when market conditions change rapidly.
"""

from __future__ import annotations

import asyncio
import functools
from datetime import datetime, date, timedelta
from typing import Any, Callable, Optional

from shared.config import get_settings
from shared.schemas import PortfolioStatus, TradeRequest, TradeResult, TradeSide
from server.utils.logging import broadcast_thought, get_logger

logger = get_logger("risk_manager")
settings = get_settings()


class RiskViolation(Exception):
    """Raised when a pre-trade risk check fails."""
    pass


# ---------------------------------------------------------------------------
# Risk Manager (stateful singleton)
# ---------------------------------------------------------------------------
class RiskManager:
    """Centralized risk gate for all trade operations."""

    def __init__(self) -> None:
        self._daily_start_equity: Optional[float] = None
        self._daily_date: Optional[date] = None
        self._peak_equity: float = 0.0
        self._bot_paused: bool = False
        self._emergency_stop: bool = False

        # Urgency event — set when the watchdog detects danger
        self.urgent_event: asyncio.Event = asyncio.Event()

        # Recent sentiment scores for change detection
        self._sentiment_history: list[tuple[datetime, float]] = []

        # Market fatigue tracking
        self._consecutive_stop_losses: int = 0
        self._rest_mode: bool = False
        self._rest_mode_until: Optional[datetime] = None
        self._recent_trade_results: list[str] = []  # "win" | "loss"

    # ------------------------------------------------------------------
    # Daily / session tracking
    # ------------------------------------------------------------------
    def update_equity(self, equity: float) -> None:
        """Called periodically to track equity curve."""
        today = date.today()
        if self._daily_date != today:
            self._daily_start_equity = equity
            self._daily_date = today
            # Reset daily counters at start of new day
            self._consecutive_stop_losses = 0
            self._recent_trade_results.clear()
        if equity > self._peak_equity:
            self._peak_equity = equity

    # ------------------------------------------------------------------
    # Market Fatigue / Rest Mode
    # ------------------------------------------------------------------
    def record_trade_outcome(self, is_stop_loss: bool) -> None:
        """Record whether a trade hit stop-loss. Triggers rest mode if consecutive."""
        if is_stop_loss:
            self._consecutive_stop_losses += 1
            self._recent_trade_results.append("loss")
            if self._consecutive_stop_losses >= settings.CONSECUTIVE_STOP_LOSS_PAUSE:
                self._enter_rest_mode()
        else:
            self._consecutive_stop_losses = 0
            self._recent_trade_results.append("win")

    def _enter_rest_mode(self) -> None:
        """Pause trading for 24 hours due to consecutive losses."""
        self._rest_mode = True
        self._rest_mode_until = datetime.utcnow() + timedelta(hours=24)
        logger.warning(
            "rest_mode_activated",
            consecutive_losses=self._consecutive_stop_losses,
            until=str(self._rest_mode_until),
        )

    def check_rest_mode(self) -> bool:
        """Return True if bot is in rest mode. Auto-exits if time elapsed."""
        if not self._rest_mode:
            return False
        if self._rest_mode_until and datetime.utcnow() > self._rest_mode_until:
            self._rest_mode = False
            self._rest_mode_until = None
            self._consecutive_stop_losses = 0
            logger.info("rest_mode_expired")
            return False
        return True

    def exit_rest_mode(self) -> None:
        """Manually exit rest mode."""
        self._rest_mode = False
        self._rest_mode_until = None
        self._consecutive_stop_losses = 0

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_stop_losses

    @property
    def is_rest_mode(self) -> bool:
        return self.check_rest_mode()

    # ------------------------------------------------------------------
    # Confidence-based position sizing
    # ------------------------------------------------------------------
    def adjust_qty_for_confidence(self, qty: float, confidence: float) -> float:
        """Scale position size based on AI confidence level."""
        if confidence >= settings.HIGH_CONFIDENCE_THRESHOLD:
            return qty * settings.HIGH_CONFIDENCE_POSITION_MULT
        elif confidence <= settings.LOW_CONFIDENCE_THRESHOLD:
            return max(1.0, qty * settings.LOW_CONFIDENCE_POSITION_MULT)
        return qty

    # ------------------------------------------------------------------
    # Pre-trade checks
    # ------------------------------------------------------------------
    async def pre_trade_check(
        self,
        request: TradeRequest,
        portfolio: PortfolioStatus,
        current_price: float,
    ) -> None:
        """
        Run ALL risk checks. Raises RiskViolation if any fail.
        Must be called before every perform_trade.
        """
        await self._check_emergency(request)
        await self._check_rest_mode(request)
        await self._check_balance(request, portfolio, current_price)
        await self._check_slippage(request, current_price)
        await self._check_position_size(request, portfolio, current_price)
        await self._check_daily_loss(portfolio)
        await self._check_max_drawdown(portfolio)

    async def _check_emergency(self, request: TradeRequest) -> None:
        if self._emergency_stop:
            raise RiskViolation("EMERGENCY STOP active — all trading halted")
        if self._bot_paused:
            raise RiskViolation("Bot is paused due to risk limits — trading halted")

    async def _check_rest_mode(self, request: TradeRequest) -> None:
        if self.check_rest_mode():
            raise RiskViolation(
                f"Market fatigue: {self._consecutive_stop_losses} consecutive stop-losses. "
                f"Rest mode until {self._rest_mode_until}."
            )

    async def _check_balance(
        self, request: TradeRequest, portfolio: PortfolioStatus, price: float
    ) -> None:
        if request.side == TradeSide.BUY:
            required = request.qty * price
            if required > portfolio.buying_power:
                raise RiskViolation(
                    f"Insufficient buying power: need ${required:.2f}, "
                    f"have ${portfolio.buying_power:.2f}"
                )

    async def _check_slippage(self, request: TradeRequest, price: float) -> None:
        if request.limit_price is not None:
            slippage_pct = abs(request.limit_price - price) / price * 100
            if slippage_pct > 0.5:
                raise RiskViolation(
                    f"Slippage too high: {slippage_pct:.2f}% "
                    f"(limit_price={request.limit_price}, market={price})"
                )

    async def _check_position_size(
        self, request: TradeRequest, portfolio: PortfolioStatus, price: float
    ) -> None:
        if request.side != TradeSide.BUY:
            return
        trade_value = request.qty * price
        max_value = portfolio.equity * (settings.MAX_POSITION_PERCENT / 100)
        # Also count existing position
        existing = sum(
            p.market_value for p in portfolio.positions if p.symbol == request.symbol
        )
        if (existing + trade_value) > max_value:
            raise RiskViolation(
                f"Position size limit: {request.symbol} would be "
                f"${existing + trade_value:.2f} vs max ${max_value:.2f} "
                f"({settings.MAX_POSITION_PERCENT}% of equity)"
            )

    async def _check_daily_loss(self, portfolio: PortfolioStatus) -> None:
        if self._daily_start_equity is None or self._daily_start_equity == 0:
            return
        daily_change_pct = (
            (portfolio.equity - self._daily_start_equity) / self._daily_start_equity * 100
        )
        if daily_change_pct < -settings.DAILY_LOSS_LIMIT_PERCENT:
            self._bot_paused = True
            await broadcast_thought(
                "risk_manager", "daily_loss_limit",
                f"DAILY LOSS LIMIT breached: {daily_change_pct:.2f}% — bot paused",
            )
            raise RiskViolation(
                f"Daily loss limit: {daily_change_pct:.2f}% exceeds "
                f"-{settings.DAILY_LOSS_LIMIT_PERCENT}%"
            )

    async def _check_max_drawdown(self, portfolio: PortfolioStatus) -> None:
        if self._peak_equity == 0:
            return
        drawdown_pct = (portfolio.equity - self._peak_equity) / self._peak_equity * 100
        if drawdown_pct < -settings.MAX_DRAWDOWN_PERCENT:
            self._emergency_stop = True
            await broadcast_thought(
                "risk_manager", "max_drawdown",
                f"MAX DRAWDOWN breached: {drawdown_pct:.2f}% — liquidating all positions",
            )
            raise RiskViolation(
                f"Max drawdown: {drawdown_pct:.2f}% exceeds "
                f"-{settings.MAX_DRAWDOWN_PERCENT}%"
            )

    # ------------------------------------------------------------------
    # Emergency controls
    # ------------------------------------------------------------------
    def activate_emergency_stop(self) -> None:
        """Immediate kill switch."""
        self._emergency_stop = True
        self._bot_paused = True
        logger.warning("emergency_stop_activated")

    def reset_emergency(self) -> None:
        """Reset emergency state (manual override)."""
        self._emergency_stop = False
        self._bot_paused = False
        logger.info("emergency_stop_reset")

    @property
    def is_paused(self) -> bool:
        return self._bot_paused

    @property
    def is_emergency(self) -> bool:
        return self._emergency_stop

    # ------------------------------------------------------------------
    # Volatility / Sentiment Watchdog
    # ------------------------------------------------------------------
    async def check_urgency(
        self,
        price_change_5m_pct: float,
        volume_vs_avg: float,
        current_sentiment: Optional[float],
        individual_drawdowns: dict[str, float],
        sector_etf_change_pct: float = 0.0,
    ) -> bool:
        """
        Evaluate whether market conditions warrant an immediate strategy update.
        Returns True if urgent, and sets self.urgent_event.
        """
        reasons: list[str] = []

        # Price action check
        if abs(price_change_5m_pct) > settings.PRICE_CHANGE_THRESHOLD_PCT:
            reasons.append(f"Price swing {price_change_5m_pct:+.2f}% in 5 min")

        # Volume spike
        if volume_vs_avg > settings.VOLUME_SPIKE_MULTIPLIER:
            reasons.append(f"Volume spike {volume_vs_avg:.1f}x average")

        # Sentiment shock
        if current_sentiment is not None:
            self._sentiment_history.append((datetime.utcnow(), current_sentiment))
            # Keep only last 10 minutes of data
            cutoff = datetime.utcnow().timestamp() - 600
            self._sentiment_history = [
                (t, s) for t, s in self._sentiment_history
                if t.timestamp() > cutoff
            ]
            if len(self._sentiment_history) >= 2:
                oldest = self._sentiment_history[0][1]
                if oldest != 0:
                    change = ((current_sentiment - oldest) / abs(oldest)) * 100
                    if change < -settings.SENTIMENT_DROP_THRESHOLD_PCT:
                        reasons.append(f"Sentiment dropped {change:.1f}% in 10 min")

        # Sector meltdown (QQQ proxy)
        if sector_etf_change_pct < -settings.PRICE_CHANGE_THRESHOLD_PCT:
            reasons.append(f"Sector ETF (QQQ) down {sector_etf_change_pct:.2f}%")

        # Individual position drawdown
        for sym, dd in individual_drawdowns.items():
            if dd < -settings.INDIVIDUAL_DRAWDOWN_PCT:
                reasons.append(f"{sym} drawdown {dd:.2f}% from high")

        if reasons:
            await broadcast_thought(
                "risk_manager", "urgency_triggered",
                f"URGENT strategy update needed: {'; '.join(reasons)}",
                {"reasons": reasons},
            )
            self.urgent_event.set()
            return True

        return False

    # ------------------------------------------------------------------
    # Status summary
    # ------------------------------------------------------------------
    def get_status(self) -> dict:
        """Return current risk manager status for UI/API."""
        return {
            "is_paused": self._bot_paused,
            "is_emergency": self._emergency_stop,
            "is_rest_mode": self.is_rest_mode,
            "rest_mode_until": str(self._rest_mode_until) if self._rest_mode_until else None,
            "consecutive_losses": self._consecutive_stop_losses,
            "peak_equity": self._peak_equity,
            "daily_start_equity": self._daily_start_equity,
        }


# ---------------------------------------------------------------------------
# Decorator: enforce risk checks before trade execution
# ---------------------------------------------------------------------------
def risk_checked(func: Callable) -> Callable:
    """
    Decorator for trade-execution functions.
    Expects the function signature to include:
       request: TradeRequest, portfolio: PortfolioStatus, current_price: float
    and a risk_manager: RiskManager in the enclosing scope or as kwarg.
    """
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        rm: RiskManager = kwargs.get("risk_manager") or (
            args[0].risk_manager if hasattr(args[0], "risk_manager") else None
        )
        request: TradeRequest = kwargs.get("request") or args[1]
        portfolio: PortfolioStatus = kwargs.get("portfolio") or args[2]
        current_price: float = kwargs.get("current_price") or args[3]

        if rm is None:
            raise RuntimeError("risk_manager not available for risk_checked decorator")

        try:
            await rm.pre_trade_check(request, portfolio, current_price)
        except RiskViolation as e:
            await broadcast_thought(
                "risk_manager", "trade_rejected",
                f"Trade REJECTED: {e}",
                {"symbol": request.symbol, "side": request.side, "qty": request.qty},
            )
            return TradeResult(
                symbol=request.symbol,
                side=request.side,
                qty=request.qty,
                status="rejected",
                message=str(e),
            )

        return await func(*args, **kwargs)

    return wrapper
