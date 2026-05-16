from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from typing import Callable, Deque, Optional, Tuple

import websockets

from app.core.config import settings

logger = logging.getLogger(__name__)


class BinanceBTCStream:
    """Live BTC bid/ask via WebSocket, multi-exchange with auto-fallback.

    Tries Coinbase -> OKX -> Binance until one works (Binance is geo-blocked on
    many cloud VMs). Maintains rolling history for momentum signals.
    """

    SOURCES: list[tuple[str, str]] = [
        ("coinbase", "wss://advanced-trade-ws.coinbase.com"),
        ("okx", "wss://ws.okx.com:8443/ws/v5/public"),
        ("binance", settings.binance_ws),
    ]

    def __init__(
        self,
        max_history_seconds: int = 600,
        on_tick: Optional[Callable[[float, float, float], None]] = None,
    ) -> None:
        self._max_history_seconds = max_history_seconds
        self._on_tick = on_tick
        self._history: Deque[Tuple[float, float]] = deque(maxlen=20000)
        self._latest: Optional[Tuple[float, float, float]] = None  # (ts_ms, bid, ask)
        self._task: Optional[asyncio.Task] = None
        self._stop = False
        self._connected = False
        self._source: str = "none"

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def source(self) -> str:
        return self._source

    @property
    def latest(self) -> Optional[Tuple[float, float, float]]:
        return self._latest

    @property
    def mid(self) -> Optional[float]:
        if self._latest is None:
            return None
        _, bid, ask = self._latest
        return (bid + ask) / 2.0

    def price_at(self, seconds_ago: float) -> Optional[float]:
        """Return mid price closest to now - seconds_ago (or None if not enough history)."""
        if not self._history:
            return None
        target = self._history[-1][0] - seconds_ago * 1000.0
        # Linear search from oldest, history is short enough
        closest = self._history[0]
        best_diff = abs(closest[0] - target)
        for ts, price in self._history:
            d = abs(ts - target)
            if d < best_diff:
                closest = (ts, price)
                best_diff = d
        return closest[1]

    def get_history(self) -> list[Tuple[float, float]]:
        return list(self._history)

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop = True
        if self._task:
            self._task.cancel()

    def _record(self, bid: float, ask: float) -> None:
        if bid <= 0 or ask <= 0:
            return
        ts_ms = time.time() * 1000.0
        mid = (bid + ask) / 2.0
        self._latest = (ts_ms, bid, ask)
        self._history.append((ts_ms, mid))
        cutoff = ts_ms - self._max_history_seconds * 1000.0
        while self._history and self._history[0][0] < cutoff:
            self._history.popleft()
        if self._on_tick is not None:
            try:
                self._on_tick(ts_ms, bid, ask)
            except Exception:
                pass

    async def _run(self) -> None:
        backoff = 1.0
        source_idx = 0
        while not self._stop:
            name, url = self.SOURCES[source_idx % len(self.SOURCES)]
            try:
                if name == "coinbase":
                    await self._run_coinbase(url)
                elif name == "okx":
                    await self._run_okx(url)
                else:
                    await self._run_binance(url)
                # Successful run (clean disconnect) — reset backoff and stay on same source.
                backoff = 1.0
            except asyncio.CancelledError:
                self._connected = False
                raise
            except Exception as exc:
                self._connected = False
                logger.warning("[btc:%s] disconnect: %s", name, exc)
                source_idx += 1
                await asyncio.sleep(min(backoff, 10.0))
                backoff = min(backoff * 1.5, 10.0)
            finally:
                self._connected = False

    async def _run_coinbase(self, url: str) -> None:
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            await ws.send(
                json.dumps(
                    {
                        "type": "subscribe",
                        "product_ids": ["BTC-USD"],
                        "channel": "ticker",
                    }
                )
            )
            self._connected = True
            self._source = "coinbase"
            logger.info("[btc] connected to coinbase")
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                events = data.get("events") or []
                for ev in events:
                    for t in ev.get("tickers") or []:
                        try:
                            bid = float(t.get("best_bid", 0) or 0)
                            ask = float(t.get("best_ask", 0) or 0)
                            if bid > 0 and ask > 0:
                                self._record(bid, ask)
                            else:
                                price = float(t.get("price", 0) or 0)
                                if price > 0:
                                    self._record(price, price)
                        except (TypeError, ValueError):
                            continue

    async def _run_okx(self, url: str) -> None:
        async with websockets.connect(url, ping_interval=25, ping_timeout=25) as ws:
            await ws.send(
                json.dumps(
                    {
                        "op": "subscribe",
                        "args": [{"channel": "tickers", "instId": "BTC-USDT"}],
                    }
                )
            )
            self._connected = True
            self._source = "okx"
            logger.info("[btc] connected to okx")
            async for raw in ws:
                if raw == "pong":
                    continue
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                for t in data.get("data") or []:
                    try:
                        bid = float(t.get("bidPx", 0) or 0)
                        ask = float(t.get("askPx", 0) or 0)
                        self._record(bid, ask)
                    except (TypeError, ValueError):
                        continue

    async def _run_binance(self, url: str) -> None:
        async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
            self._connected = True
            self._source = "binance"
            logger.info("[btc] connected to binance")
            async for raw in ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                try:
                    bid = float(data.get("b", 0))
                    ask = float(data.get("a", 0))
                    self._record(bid, ask)
                except (TypeError, ValueError):
                    continue
