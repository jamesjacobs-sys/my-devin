from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from app.core.config import settings
from app.core.database import AsyncSessionLocal
from app.data.binance import BinanceBTCStream
from app.data.polymarket import PolymarketClient, PolymarketMarketWS
from app.data.schemas import MarketInfo, SignalOutput
from app.engine.signals import MarketSnapshot, evaluate_market
from app.engine.trader import PaperTrader

logger = logging.getLogger(__name__)


class BotOrchestrator:
    """Wires together Binance + Polymarket WS + signal engine + trader.

    Public state for the API to read:
      - `markets`: latest MarketInfo dict keyed by condition_id
      - `btc`: BinanceBTCStream instance
      - `recent_signals`: ring buffer of last N signals
      - `live_events`: dict-keyed pubsub for the frontend WS
    """

    def __init__(self) -> None:
        self.pm = PolymarketClient()
        self.btc = BinanceBTCStream()
        self.market_ws = PolymarketMarketWS(self._on_clob_event)
        self.markets: Dict[str, MarketInfo] = {}
        self._token_to_market: Dict[str, str] = {}  # token_id -> condition_id
        self.recent_signals: List[SignalOutput] = []
        self._btc_at_window_open: Dict[int, float] = {}  # market_end_unix -> btc open price
        self._refresh_task: Optional[asyncio.Task] = None
        self._signal_task: Optional[asyncio.Task] = None
        self._resolver_task: Optional[asyncio.Task] = None
        self._stop = False
        self.event_subscribers: List[asyncio.Queue] = []

    # ----- lifecycle -----

    async def start(self) -> None:
        await self.btc.start()
        await self._refresh_markets(initial=True)
        self._refresh_task = asyncio.create_task(self._refresh_loop())
        self._signal_task = asyncio.create_task(self._signal_loop())
        self._resolver_task = asyncio.create_task(self._resolver_loop())

    async def stop(self) -> None:
        self._stop = True
        for t in [self._refresh_task, self._signal_task, self._resolver_task]:
            if t:
                t.cancel()
        await self.market_ws.stop()
        await self.btc.stop()
        await self.pm.close()

    # ----- subscriptions -----

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=200)
        self.event_subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self.event_subscribers:
            self.event_subscribers.remove(q)

    def _broadcast(self, event: dict) -> None:
        for q in list(self.event_subscribers):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    # ----- background loops -----

    async def _refresh_loop(self) -> None:
        while not self._stop:
            try:
                await asyncio.sleep(20)
                await self._refresh_markets()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("refresh loop err: %s", exc)

    async def _refresh_markets(self, initial: bool = False) -> None:
        markets = await self.pm.fetch_active_5m_btc_markets(limit=20)
        # Keep only markets that haven't ended yet
        now = int(time.time())
        markets = [m for m in markets if (m.end_unix or 0) > now - 30]
        new_lookup: Dict[str, MarketInfo] = {m.condition_id: m for m in markets}
        new_token_lookup: Dict[str, str] = {}
        for m in markets:
            new_token_lookup[m.token_up_id] = m.condition_id
            new_token_lookup[m.token_down_id] = m.condition_id

        self.markets = new_lookup
        self._token_to_market = new_token_lookup

        # Capture BTC open price for each market window
        btc_mid = self.btc.mid
        if btc_mid is not None:
            for m in markets:
                if m.end_unix is None:
                    continue
                window_open = m.end_unix - 300
                # Approximate the open price: pull from history at offset
                offset = max(0.0, time.time() - window_open)
                price = self.btc.price_at(offset) or btc_mid
                self._btc_at_window_open.setdefault(m.end_unix, price)

        # Update WS subscription
        asset_ids = list({m.token_up_id for m in markets} | {m.token_down_id for m in markets})
        if asset_ids:
            await self.market_ws.start(asset_ids)
            if not initial:
                await self.market_ws.update_assets(asset_ids)

        self._broadcast({"type": "markets", "markets": [m.model_dump() for m in markets]})

    def _on_clob_event(self, ev: Dict[str, Any]) -> None:
        et = ev.get("event_type")
        try:
            if et == "book":
                asset_id = ev.get("asset_id")
                bids = ev.get("bids") or []
                asks = ev.get("asks") or []
                self._update_token_book(asset_id, bids, asks)
            elif et == "price_change":
                for ch in ev.get("price_changes") or []:
                    self._update_token_best(
                        ch.get("asset_id"),
                        ch.get("best_bid"),
                        ch.get("best_ask"),
                    )
            elif et == "best_bid_ask":
                self._update_token_best(
                    ev.get("asset_id"), ev.get("best_bid"), ev.get("best_ask")
                )
            elif et == "last_trade_price":
                self._update_token_last(ev.get("asset_id"), ev.get("price"))
        except Exception as exc:
            logger.debug("clob event handler err: %s", exc)

    def _update_token_book(self, asset_id: str, bids: list, asks: list) -> None:
        cond = self._token_to_market.get(str(asset_id))
        if not cond:
            return
        m = self.markets.get(cond)
        if not m:
            return
        best_bid = max((float(b["price"]) for b in bids), default=None)
        best_ask = min((float(a["price"]) for a in asks), default=None)
        if str(asset_id) == m.token_up_id:
            if best_bid is not None:
                m.token_up_best_bid = best_bid
            if best_ask is not None:
                m.token_up_best_ask = best_ask
                m.token_up_price = best_ask  # use ask as mark for UP buy side
        elif str(asset_id) == m.token_down_id:
            if best_bid is not None:
                m.token_down_best_bid = best_bid
            if best_ask is not None:
                m.token_down_best_ask = best_ask
                m.token_down_price = best_ask
        self._broadcast({"type": "book", "condition_id": cond, "market": m.model_dump()})

    def _update_token_best(self, asset_id: Optional[str], best_bid: Any, best_ask: Any) -> None:
        if asset_id is None:
            return
        cond = self._token_to_market.get(str(asset_id))
        if not cond:
            return
        m = self.markets.get(cond)
        if not m:
            return
        try:
            bb = float(best_bid) if best_bid is not None else None
            ba = float(best_ask) if best_ask is not None else None
        except (TypeError, ValueError):
            return
        if str(asset_id) == m.token_up_id:
            if bb is not None:
                m.token_up_best_bid = bb
            if ba is not None:
                m.token_up_best_ask = ba
                m.token_up_price = ba
        elif str(asset_id) == m.token_down_id:
            if bb is not None:
                m.token_down_best_bid = bb
            if ba is not None:
                m.token_down_best_ask = ba
                m.token_down_price = ba
        self._broadcast({"type": "book", "condition_id": cond, "market": m.model_dump()})

    def _update_token_last(self, asset_id: Optional[str], price: Any) -> None:
        if asset_id is None or price is None:
            return
        cond = self._token_to_market.get(str(asset_id))
        if not cond:
            return
        m = self.markets.get(cond)
        if not m:
            return
        try:
            p = float(price)
        except (TypeError, ValueError):
            return
        if str(asset_id) == m.token_up_id:
            m.token_up_price = p
        elif str(asset_id) == m.token_down_id:
            m.token_down_price = p
        self._broadcast({"type": "trade", "condition_id": cond, "market": m.model_dump()})

    async def _signal_loop(self) -> None:
        while not self._stop:
            try:
                await asyncio.sleep(5)
                await self._evaluate_all()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("signal loop err: %s", exc)

    async def _evaluate_all(self) -> None:
        btc_mid = self.btc.mid
        if btc_mid is None:
            return
        history = self.btc.get_history()
        now = int(time.time())
        async with AsyncSessionLocal() as session:
            trader = PaperTrader(session)
            bot = await trader.get_settings()
            for m in list(self.markets.values()):
                if not m.end_unix or m.end_unix <= now:
                    continue
                if m.end_unix - now > 295:
                    # too early, market just started, signals are too noisy
                    continue
                if m.end_unix - now < 5:
                    # too late, fill risk too high
                    continue
                btc_open = self._btc_at_window_open.get(m.end_unix)
                snap = MarketSnapshot(
                    market=m,
                    btc_price=btc_mid,
                    btc_window_open_price=btc_open,
                    btc_price_at_market_open=btc_open,
                    btc_history=history,
                )
                sig = evaluate_market(
                    snap,
                    min_edge=bot.min_edge,
                    dislocation_threshold=bot.dislocation_threshold,
                )
                if sig is None:
                    continue
                self.recent_signals.append(sig)
                self.recent_signals = self.recent_signals[-200:]
                await trader.log_signal(sig)
                self._broadcast({"type": "signal", "signal": sig.model_dump(mode="json")})

                if bot.auto_trade:
                    trade = await trader.place_paper_trade(sig, m)
                    if trade:
                        self._broadcast(
                            {
                                "type": "trade",
                                "action": "open",
                                "trade": {
                                    "id": trade.id,
                                    "outcome": trade.outcome,
                                    "size": trade.size,
                                    "entry_price": trade.entry_price,
                                    "market_slug": trade.market_slug,
                                    "signal_type": trade.signal_type,
                                    "mode": trade.mode,
                                },
                            }
                        )

            # Always record a balance snapshot
            await trader.record_balance_snapshot(bot.mode, self.markets)

    async def _resolver_loop(self) -> None:
        while not self._stop:
            try:
                await asyncio.sleep(5)
                await self._resolve_due()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("resolver loop err: %s", exc)

    async def _resolve_due(self) -> None:
        # Snapshot the BTC price each time a 5-minute window closes
        now = int(time.time())
        btc_mid = self.btc.mid
        if btc_mid is None:
            return
        # Index any just-closed windows
        for end_unix in list(self._btc_at_window_open.keys()):
            if end_unix <= now and end_unix not in getattr(self, "_btc_at_close", {}):
                if not hasattr(self, "_btc_at_close"):
                    self._btc_at_close: Dict[int, float] = {}
                self._btc_at_close[end_unix] = btc_mid

        async with AsyncSessionLocal() as session:
            trader = PaperTrader(session)
            resolved = await trader.resolve_due_trades(
                self.markets, getattr(self, "_btc_at_close", {})
            )
            for t in resolved:
                self._broadcast(
                    {
                        "type": "trade",
                        "action": "resolved",
                        "trade": {
                            "id": t.id,
                            "outcome": t.outcome,
                            "size": t.size,
                            "entry_price": t.entry_price,
                            "exit_price": t.exit_price,
                            "pnl": t.pnl,
                            "status": t.status,
                            "market_slug": t.market_slug,
                            "mode": t.mode,
                        },
                    }
                )


orchestrator = BotOrchestrator()
