# =============================================================================
# Project Scolecite - Trading Engine (Alpaca Markets)
# DISCLAIMER: For educational/research purposes only.
# The authors are NOT responsible for any financial losses.
# =============================================================================
"""
Alpaca-backed trading engine with:
 - Paper/live mode switching via TRADING_MODE env var
 - Real-time market data (Alpaca + Polygon fallback)
 - Risk-checked order execution
 - Portfolio snapshot retrieval
 - Multi-timeframe data with pandas resampling cache
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Optional

import httpx
import pandas as pd
from tenacity import retry, stop_after_attempt, wait_exponential

from shared.config import get_settings
from shared.schemas import (
    MarketCandle,
    MarketSnapshot,
    PortfolioStatus,
    PositionInfo,
    TradeRequest,
    TradeResult,
    TradeSide,
    OrderType,
    Timeframe,
)
from server.core.risk_manager import RiskManager, risk_checked
from server.utils.logging import broadcast_thought, get_logger
from server.database import async_session_factory, TradeRecord

logger = get_logger("trading_engine")
settings = get_settings()

# Mapping Timeframe enum to Alpaca API timeframe strings
_ALPACA_TF_MAP = {
    Timeframe.MIN_1: "1Min",
    Timeframe.MIN_5: "5Min",
    Timeframe.MIN_15: "15Min",
    Timeframe.HOUR_1: "1Hour",
    Timeframe.DAY_1: "1Day",
    Timeframe.WEEK_1: "1Week",
    Timeframe.MONTH_1: "1Month",
    Timeframe.YEAR_1: "1Day",  # 1Y uses daily candles with long lookback
}

# How far back to look for each timeframe
_LOOKBACK_MAP = {
    Timeframe.MIN_1: timedelta(hours=6),
    Timeframe.MIN_5: timedelta(hours=12),
    Timeframe.MIN_15: timedelta(days=2),
    Timeframe.HOUR_1: timedelta(days=5),
    Timeframe.DAY_1: timedelta(days=90),
    Timeframe.WEEK_1: timedelta(days=365),
    Timeframe.MONTH_1: timedelta(days=730),
    Timeframe.YEAR_1: timedelta(days=365),
}


class TradingEngine:
    """
    Broker interface to Alpaca Markets.
    All orders pass through RiskManager before submission.
    """

    def __init__(self, risk_manager: RiskManager) -> None:
        self.risk_manager = risk_manager
        self._http: Optional[httpx.AsyncClient] = None
        self._data_http: Optional[httpx.AsyncClient] = None

        # Alpaca endpoints
        self._base_url = settings.APCA_API_BASE_URL.rstrip("/")
        self._data_url = "https://data.alpaca.markets"
        self._headers = {
            "APCA-API-KEY-ID": settings.APCA_API_KEY_ID,
            "APCA-API-SECRET-KEY": settings.APCA_API_SECRET_KEY,
            "Content-Type": "application/json",
        }

        # Multi-timeframe candle cache: {(symbol, timeframe): (timestamp, list[MarketCandle])}
        self._candle_cache: dict[tuple[str, str], tuple[datetime, list[MarketCandle]]] = {}
        self._cache_ttl = timedelta(minutes=2)  # cache freshness

        # All tradable ticker symbols (loaded once at startup)
        self._all_tickers: list[str] = []

        # Snapshot cache: {symbol: (timestamp, MarketSnapshot)}
        self._snapshot_cache: dict[str, tuple[datetime, MarketSnapshot]] = {}
        self._snapshot_ttl = timedelta(seconds=30)  # cache for 30s

    async def start(self) -> None:
        """Initialise HTTP clients."""
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers,
            timeout=30.0,
        )
        self._data_http = httpx.AsyncClient(
            base_url=self._data_url,
            headers=self._headers,
            timeout=30.0,
        )
        await self._load_all_tickers()
        await broadcast_thought(
            "engine", "start",
            f"Trading engine started (mode={settings.TRADING_MODE}, "
            f"base_url={self._base_url})",
        )

    async def _load_all_tickers(self) -> None:
        """Load all tradable US equity tickers from Alpaca (once)."""
        if self._all_tickers:
            return
        try:
            resp = await self._http.get(  # type: ignore[union-attr]
                "/v2/assets",
                params={"status": "active", "asset_class": "us_equity"},
            )
            if resp.status_code == 200:
                assets = resp.json()
                self._all_tickers = sorted(
                    a["symbol"] for a in assets
                    if a.get("tradable") and a.get("symbol")
                )
                logger.info("loaded_tickers", count=len(self._all_tickers))
            else:
                logger.warning("ticker_load_failed", status=resp.status_code)
        except Exception as e:
            logger.warning("ticker_load_error", error=str(e))

    def search_tickers(self, query: str, limit: int = 20) -> list[str]:
        """Filter in-memory ticker list by prefix (case-insensitive)."""
        if not query:
            return self._all_tickers[:limit]
        q = query.upper()
        return [t for t in self._all_tickers if t.startswith(q)][:limit]

    async def stop(self) -> None:
        if self._http:
            await self._http.aclose()
        if self._data_http:
            await self._data_http.aclose()

    # ------------------------------------------------------------------
    # Market Data
    # ------------------------------------------------------------------
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=15))
    async def get_snapshot(self, symbol: str) -> MarketSnapshot:
        """Get latest quote + recent candles for a symbol (cached 30s)."""
        # Check snapshot cache
        if symbol in self._snapshot_cache:
            cached_time, cached_snap = self._snapshot_cache[symbol]
            if datetime.utcnow() - cached_time < self._snapshot_ttl:
                return cached_snap

        if not self._data_http:
            return MarketSnapshot(symbol=symbol, price=0.0)

        try:
            # Latest trade
            trade_resp = await self._data_http.get(
                f"/v2/stocks/{symbol}/trades/latest"
            )
            trade_data = trade_resp.json()
            price = trade_data.get("trade", {}).get("p", 0.0)

            # Recent bars (1-hour candles, last 24h)
            end = datetime.utcnow()
            start = end - timedelta(hours=24)
            bars_resp = await self._data_http.get(
                f"/v2/stocks/{symbol}/bars",
                params={
                    "timeframe": "1Hour",
                    "start": start.isoformat() + "Z",
                    "end": end.isoformat() + "Z",
                    "limit": 24,
                },
            )
            bars_data = bars_resp.json()

            candles = []
            for bar in bars_data.get("bars", []):
                candles.append(MarketCandle(
                    symbol=symbol,
                    timestamp=bar["t"],
                    open=bar["o"],
                    high=bar["h"],
                    low=bar["l"],
                    close=bar["c"],
                    volume=bar["v"],
                    vwap=bar.get("vw"),
                    timeframe="1Hour",
                ))

            # Compute change % from previous close
            change_pct = 0.0
            if len(candles) >= 2:
                prev_close = candles[-2].close
                if prev_close > 0:
                    change_pct = (price - prev_close) / prev_close * 100

            # Average volume
            avg_vol = (
                sum(c.volume for c in candles) // len(candles) if candles else 0
            )

            snap = MarketSnapshot(
                symbol=symbol,
                price=price,
                change_pct=change_pct,
                volume=candles[-1].volume if candles else 0,
                avg_volume=avg_vol,
                candles=candles,
            )
            self._snapshot_cache[symbol] = (datetime.utcnow(), snap)
            return snap
        except Exception as e:
            logger.warning("snapshot_failed", symbol=symbol, error=str(e))
            return MarketSnapshot(symbol=symbol, price=0.0)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=15))
    async def get_latest_price(self, symbol: str) -> float:
        """Quick latest-trade price lookup."""
        if not self._data_http:
            return 0.0
        try:
            resp = await self._data_http.get(f"/v2/stocks/{symbol}/trades/latest")
            return resp.json().get("trade", {}).get("p", 0.0)
        except Exception:
            return 0.0

    # ------------------------------------------------------------------
    # Multi-Timeframe Candle Data
    # ------------------------------------------------------------------
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=15))
    async def get_candles(
        self, symbol: str, timeframe: Timeframe = Timeframe.HOUR_1, limit: int = 100
    ) -> list[MarketCandle]:
        """
        Fetch candles for a given symbol and timeframe.
        Uses caching + pandas resampling for derived timeframes.
        """
        cache_key = (symbol, timeframe.value)

        # Check cache
        if cache_key in self._candle_cache:
            cached_time, cached_candles = self._candle_cache[cache_key]
            if datetime.utcnow() - cached_time < self._cache_ttl:
                return cached_candles[:limit]

        if not self._data_http:
            return []

        try:
            lookback = _LOOKBACK_MAP.get(timeframe, timedelta(days=5))
            end = datetime.utcnow()
            start = end - lookback
            alpaca_tf = _ALPACA_TF_MAP.get(timeframe, "1Hour")

            # For timeframes Alpaca supports natively, fetch directly
            if timeframe in (Timeframe.MIN_1, Timeframe.MIN_5, Timeframe.MIN_15,
                             Timeframe.HOUR_1, Timeframe.DAY_1):
                bars_resp = await self._data_http.get(
                    f"/v2/stocks/{symbol}/bars",
                    params={
                        "timeframe": alpaca_tf,
                        "start": start.isoformat() + "Z",
                        "end": end.isoformat() + "Z",
                        "limit": limit,
                    },
                )
                bars_data = bars_resp.json()
                candles = [
                    MarketCandle(
                        symbol=symbol,
                        timestamp=bar["t"],
                        open=bar["o"],
                        high=bar["h"],
                        low=bar["l"],
                        close=bar["c"],
                        volume=bar["v"],
                        vwap=bar.get("vw"),
                        timeframe=timeframe.value,
                    )
                    for bar in bars_data.get("bars", [])
                ]
            else:
                # Resample from daily data for weekly/monthly
                candles = await self._resample_candles(symbol, timeframe, start, end, limit)

            self._candle_cache[cache_key] = (datetime.utcnow(), candles)
            return candles[:limit]

        except Exception as e:
            logger.warning("candles_fetch_failed", symbol=symbol, timeframe=timeframe.value, error=str(e))
            return []

    async def _resample_candles(
        self, symbol: str, target_tf: Timeframe,
        start: datetime, end: datetime, limit: int,
    ) -> list[MarketCandle]:
        """Resample daily candles to weekly/monthly using pandas."""
        # Fetch daily data
        bars_resp = await self._data_http.get(
            f"/v2/stocks/{symbol}/bars",
            params={
                "timeframe": "1Day",
                "start": start.isoformat() + "Z",
                "end": end.isoformat() + "Z",
                "limit": 1000,
            },
        )
        bars_data = bars_resp.json()
        bars = bars_data.get("bars", [])
        if not bars:
            return []

        df = pd.DataFrame(bars)
        df["t"] = pd.to_datetime(df["t"])
        df = df.set_index("t")

        # Determine resample rule
        rule = "W" if target_tf == Timeframe.WEEK_1 else "ME"

        resampled = df.resample(rule).agg({
            "o": "first",
            "h": "max",
            "l": "min",
            "c": "last",
            "v": "sum",
        }).dropna()

        candles = []
        for ts, row in resampled.iterrows():
            candles.append(MarketCandle(
                symbol=symbol,
                timestamp=ts.to_pydatetime(),
                open=float(row["o"]),
                high=float(row["h"]),
                low=float(row["l"]),
                close=float(row["c"]),
                volume=int(row["v"]),
                timeframe=target_tf.value,
            ))

        return candles[-limit:]

    # ------------------------------------------------------------------
    # Portfolio
    # ------------------------------------------------------------------
    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=15))
    async def get_portfolio(self) -> PortfolioStatus:
        """Fetch account + positions from Alpaca."""
        if not self._http:
            return PortfolioStatus()

        try:
            # Account info
            acct_resp = await self._http.get("/v2/account")
            acct = acct_resp.json()

            equity = float(acct.get("equity", 0))
            cash = float(acct.get("cash", 0))
            buying_power = float(acct.get("buying_power", 0))

            # Update risk manager equity tracking
            self.risk_manager.update_equity(equity)

            # Positions
            pos_resp = await self._http.get("/v2/positions")
            positions_data = pos_resp.json() if pos_resp.status_code == 200 else []

            positions = []
            for p in positions_data:
                positions.append(PositionInfo(
                    symbol=p["symbol"],
                    qty=float(p.get("qty", 0)),
                    avg_entry_price=float(p.get("avg_entry_price", 0)),
                    current_price=float(p.get("current_price", 0)),
                    unrealized_pl=float(p.get("unrealized_pl", 0)),
                    unrealized_pl_pct=float(p.get("unrealized_plpc", 0)) * 100,
                    market_value=float(p.get("market_value", 0)),
                ))

            # Daily P&L from account data
            last_equity = float(acct.get("last_equity", equity))
            daily_pl = equity - last_equity
            daily_pl_pct = (daily_pl / last_equity * 100) if last_equity else 0

            return PortfolioStatus(
                equity=equity,
                cash=cash,
                buying_power=buying_power,
                daily_pl=daily_pl,
                daily_pl_pct=daily_pl_pct,
                positions=positions,
            )
        except Exception as e:
            logger.warning("portfolio_fetch_failed", error=str(e))
            return PortfolioStatus()

    # ------------------------------------------------------------------
    # Order Execution (risk-checked)
    # ------------------------------------------------------------------
    @risk_checked
    async def execute_trade(
        self,
        request: TradeRequest,
        portfolio: PortfolioStatus,
        current_price: float,
    ) -> TradeResult:
        """
        Submit an order to Alpaca.
        The @risk_checked decorator runs all safety checks first.
        """
        await broadcast_thought(
            "engine", "execute_trade",
            f"Executing {request.side.value} {request.qty} {request.symbol}",
            {"order_type": request.order_type.value, "reason": request.reason},
        )

        # In paper mode that's still handled by Alpaca paper endpoint
        if not self._http:
            return TradeResult(
                symbol=request.symbol,
                side=request.side,
                qty=request.qty,
                status="error",
                message="Trading engine not connected",
            )

        order_body: dict[str, Any] = {
            "symbol": request.symbol,
            "qty": str(request.qty),
            "side": request.side.value,
            "type": request.order_type.value,
            "time_in_force": "day",
        }
        if request.order_type == OrderType.LIMIT and request.limit_price:
            order_body["limit_price"] = str(request.limit_price)
        if settings.ALLOW_EXTENDED_HOURS:
            order_body["extended_hours"] = True

        try:
            resp = await self._http.post("/v2/orders", json=order_body)
            data = resp.json()

            if resp.status_code in (200, 201):
                result = TradeResult(
                    order_id=data.get("id", ""),
                    symbol=request.symbol,
                    side=request.side,
                    qty=request.qty,
                    filled_price=float(data.get("filled_avg_price", 0)) or None,
                    status=data.get("status", "accepted"),
                    message=f"Order submitted: {data.get('id', '')}",
                )
            else:
                result = TradeResult(
                    symbol=request.symbol,
                    side=request.side,
                    qty=request.qty,
                    status="error",
                    message=data.get("message", str(data)),
                )
        except Exception as e:
            result = TradeResult(
                symbol=request.symbol,
                side=request.side,
                qty=request.qty,
                status="error",
                message=str(e),
            )

        # Persist trade record
        await self._save_trade(request, result)

        await broadcast_thought(
            "engine", "trade_result",
            f"Trade result: {result.status} — {result.message}",
            {"order_id": result.order_id, "filled_price": result.filled_price},
        )

        return result

    # ------------------------------------------------------------------
    # Liquidation (emergency)
    # ------------------------------------------------------------------
    async def liquidate_all(self) -> list[TradeResult]:
        """Close all positions immediately (emergency kill switch)."""
        await broadcast_thought(
            "engine", "liquidate_all",
            "EMERGENCY: Liquidating all positions",
        )

        if not self._http:
            return []

        try:
            resp = await self._http.delete("/v2/positions")
            if resp.status_code == 207:
                data = resp.json()
                results = []
                for item in data:
                    body = item.get("body", {})
                    results.append(TradeResult(
                        order_id=body.get("id", ""),
                        symbol=body.get("symbol", ""),
                        side=TradeSide.SELL,
                        qty=float(body.get("qty", 0)),
                        status=body.get("status", "submitted"),
                        message="Emergency liquidation",
                    ))
                return results
            return []
        except Exception as e:
            logger.error("liquidation_failed", error=str(e))
            return []

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    async def _save_trade(self, request: TradeRequest, result: TradeResult) -> None:
        """Persist trade to database."""
        try:
            async with async_session_factory() as session:
                record = TradeRecord(
                    order_id=result.order_id or f"local-{datetime.utcnow().timestamp()}",
                    symbol=request.symbol,
                    side=request.side.value,
                    qty=request.qty,
                    filled_price=result.filled_price,
                    status=result.status,
                    reason=request.reason,
                    message=result.message,
                    confidence=request.confidence,
                )
                session.add(record)
                await session.commit()
        except Exception as e:
            logger.warning("trade_save_failed", error=str(e))

    async def get_trade_history(self, limit: int = 50) -> list[dict]:
        """Retrieve recent trades from DB."""
        from sqlalchemy import select
        try:
            async with async_session_factory() as session:
                stmt = (
                    select(TradeRecord)
                    .order_by(TradeRecord.created_at.desc())
                    .limit(limit)
                )
                result = await session.execute(stmt)
                records = result.scalars().all()
                return [
                    {
                        "order_id": r.order_id,
                        "symbol": r.symbol,
                        "side": r.side,
                        "qty": r.qty,
                        "filled_price": r.filled_price,
                        "status": r.status,
                        "reason": r.reason,
                        "message": r.message,
                        "confidence": r.confidence,
                        "created_at": str(r.created_at),
                    }
                    for r in records
                ]
        except Exception as e:
            logger.warning("trade_history_failed", error=str(e))
            return []
