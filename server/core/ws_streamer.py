# =============================================================================
# Project Scolecite - Alpaca WebSocket Price Streamer
# DISCLAIMER: For educational/research purposes only.
# The authors are NOT responsible for any financial losses.
# =============================================================================
"""
Real-time price streaming via Alpaca WebSocket (wss://stream.data.alpaca.markets).
Subscribes to trade updates for tracked symbols and pushes price changes
to SSE subscribers instantly, replacing poll-based price updates.

Usage:
    streamer = AlpacaWSStreamer(callback=on_price_update)
    await streamer.start()
    await streamer.subscribe(["AAPL", "TSLA", "NVDA"])
    ...
    await streamer.stop()
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Callable, Coroutine, Optional

import websockets
from websockets.exceptions import ConnectionClosed

from shared.config import get_settings
from server.utils.logging import get_logger

logger = get_logger("ws_streamer")
settings = get_settings()

# Alpaca real-time data WebSocket endpoints
_WS_IEX_URL = "wss://stream.data.alpaca.markets/v2/iex"
_WS_SIP_URL = "wss://stream.data.alpaca.markets/v2/sip"


class AlpacaWSStreamer:
    """
    Maintains a persistent WebSocket connection to Alpaca for real-time
    trade/quote updates.  Automatically reconnects on disconnection.
    """

    def __init__(
        self,
        callback: Callable[[dict[str, Any]], Coroutine],
        use_sip: bool = False,
    ) -> None:
        """
        Args:
            callback: async function called with each price update dict:
                      {"symbol": "AAPL", "price": 189.50, "size": 100,
                       "timestamp": "...", "type": "trade"}
            use_sip: Use SIP feed (paid) instead of IEX (free).
        """
        self._callback = callback
        self._url = _WS_SIP_URL if use_sip else _WS_IEX_URL
        self._ws: Optional[Any] = None
        self._task: Optional[asyncio.Task] = None
        self._subscribed: set[str] = set()
        self._running = False
        self._reconnect_delay = 1.0  # seconds, exponential backoff

    async def start(self) -> None:
        """Start the WebSocket listener loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._run_loop(), name="ws_streamer")
        logger.info("ws_streamer_started")

    async def stop(self) -> None:
        """Gracefully close the WebSocket connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("ws_streamer_stopped")

    async def subscribe(self, symbols: list[str]) -> None:
        """Subscribe to trade updates for the given symbols."""
        new_symbols = set(s.upper() for s in symbols)
        to_add = new_symbols - self._subscribed
        to_remove = self._subscribed - new_symbols

        if to_remove and self._ws:
            try:
                await self._ws.send(json.dumps({
                    "action": "unsubscribe",
                    "trades": list(to_remove),
                }))
            except Exception:
                pass

        if to_add and self._ws:
            try:
                await self._ws.send(json.dumps({
                    "action": "subscribe",
                    "trades": list(to_add),
                }))
            except Exception:
                pass

        self._subscribed = new_symbols

    async def _run_loop(self) -> None:
        """Main reconnection loop."""
        while self._running:
            try:
                await self._connect_and_listen()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("ws_connection_error", error=str(e))

            if self._running:
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, 30.0)

    async def _connect_and_listen(self) -> None:
        """Connect, authenticate, subscribe, and process messages."""
        async with websockets.connect(self._url) as ws:
            self._ws = ws
            self._reconnect_delay = 1.0  # reset on successful connect

            # Read auth challenge
            auth_msg = await ws.recv()
            logger.info("ws_connected", msg=str(auth_msg)[:100])

            # Authenticate
            await ws.send(json.dumps({
                "action": "auth",
                "key": settings.APCA_API_KEY_ID,
                "secret": settings.APCA_API_SECRET_KEY,
            }))
            auth_resp = await ws.recv()
            resp_data = json.loads(auth_resp)
            if isinstance(resp_data, list):
                for item in resp_data:
                    if item.get("msg") == "authenticated":
                        logger.info("ws_authenticated")
                        break
                    elif item.get("msg") == "auth_failed":
                        logger.error("ws_auth_failed")
                        return

            # Subscribe to current symbols
            if self._subscribed:
                await ws.send(json.dumps({
                    "action": "subscribe",
                    "trades": list(self._subscribed),
                }))

            # Listen for messages
            async for raw_msg in ws:
                if not self._running:
                    break
                try:
                    messages = json.loads(raw_msg)
                    if not isinstance(messages, list):
                        messages = [messages]
                    for msg in messages:
                        msg_type = msg.get("T")
                        if msg_type == "t":  # trade update
                            update = {
                                "symbol": msg.get("S", ""),
                                "price": msg.get("p", 0.0),
                                "size": msg.get("s", 0),
                                "timestamp": msg.get("t", ""),
                                "type": "trade",
                            }
                            try:
                                await self._callback(update)
                            except Exception:
                                pass
                except json.JSONDecodeError:
                    pass
                except ConnectionClosed:
                    break
