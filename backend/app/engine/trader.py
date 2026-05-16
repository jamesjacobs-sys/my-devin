from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.data.models import BalanceSnapshot, BotSettings, SignalLog, Trade
from app.data.schemas import MarketInfo, SignalOutput

logger = logging.getLogger(__name__)


class PaperTrader:
    """Executes paper trades against real Polymarket prices.

    Live execution is gated behind `mode == "live"` AND a `POLYMARKET_PRIVATE_KEY`
    being present. The actual signing flow is implemented in `live_executor`.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_settings(self) -> BotSettings:
        stmt = select(BotSettings).where(BotSettings.id == 1)
        res = await self._session.execute(stmt)
        bot = res.scalar_one_or_none()
        if bot is None:
            bot = BotSettings(
                id=1,
                mode="paper",
                auto_trade=False,
                max_position_usd=settings.paper_max_position,
                max_concurrent=settings.paper_max_concurrent,
                min_edge=settings.signal_min_edge,
                dislocation_threshold=settings.signal_dislocation_threshold,
                starting_balance=settings.paper_starting_balance,
            )
            self._session.add(bot)
            await self._session.commit()
        return bot

    async def get_open_trades(self, mode: Optional[str] = None) -> List[Trade]:
        stmt = select(Trade).where(Trade.status == "OPEN")
        if mode is not None:
            stmt = stmt.where(Trade.mode == mode)
        res = await self._session.execute(stmt)
        return list(res.scalars().all())

    async def get_realized_pnl(self, mode: str) -> float:
        stmt = select(Trade).where(Trade.mode == mode, Trade.status != "OPEN")
        res = await self._session.execute(stmt)
        return float(sum((t.pnl or 0.0) for t in res.scalars()))

    async def balance(self, mode: str) -> float:
        bot = await self.get_settings()
        realized = await self.get_realized_pnl(mode)
        open_trades = await self.get_open_trades(mode)
        spent_open = sum(t.entry_price * t.size + t.fee for t in open_trades)
        return bot.starting_balance + realized - spent_open

    async def equity(self, mode: str, market_lookup: dict[str, MarketInfo]) -> tuple[float, float]:
        """Return (equity, unrealized_pnl) using current token mark prices."""
        bot = await self.get_settings()
        realized = await self.get_realized_pnl(mode)
        open_trades = await self.get_open_trades(mode)
        unrealized = 0.0
        spent_open = 0.0
        for t in open_trades:
            spent_open += t.entry_price * t.size + t.fee
            mk = market_lookup.get(t.market_condition_id)
            if mk is None:
                mark = t.entry_price
            else:
                mark = mk.token_up_price if t.outcome == "UP" else mk.token_down_price
            unrealized += (mark - t.entry_price) * t.size
        equity = bot.starting_balance + realized - spent_open + (spent_open - sum(t.fee for t in open_trades)) + unrealized
        # simpler: equity = balance + market_value_of_positions
        market_value = sum(
            (
                (market_lookup.get(t.market_condition_id).token_up_price if (t.outcome == "UP" and t.market_condition_id in market_lookup) else (market_lookup.get(t.market_condition_id).token_down_price if t.market_condition_id in market_lookup else t.entry_price))
                * t.size
            )
            for t in open_trades
        )
        balance = bot.starting_balance + realized - spent_open
        equity = balance + market_value
        return equity, unrealized

    async def place_paper_trade(
        self,
        signal: SignalOutput,
        market: MarketInfo,
    ) -> Optional[Trade]:
        bot = await self.get_settings()
        # Concurrency cap
        open_trades = await self.get_open_trades(mode=bot.mode)
        if len(open_trades) >= bot.max_concurrent:
            return None
        # Already have a position in this market?
        if any(t.market_condition_id == market.condition_id for t in open_trades):
            return None

        if signal.direction == "UP":
            entry_price = market.token_up_best_ask or market.token_up_price
            token_id = market.token_up_id
        else:
            entry_price = market.token_down_best_ask or market.token_down_price
            token_id = market.token_down_id

        if entry_price <= 0 or entry_price >= 1:
            return None

        budget = min(bot.max_position_usd, signal.suggested_size)
        if budget <= 0:
            return None
        size = budget / entry_price

        def _fee(sz: float) -> float:
            f = entry_price * 0.25 * (entry_price * (1.0 - entry_price)) ** 2 * sz
            f += entry_price * sz * settings.polymarket_taker_fee_rate
            return f

        fee = _fee(size)

        balance = await self.balance(bot.mode)
        cost = entry_price * size + fee
        if cost > balance:
            fee_per_share = _fee(1.0)
            size = max(0.0, balance / (entry_price + fee_per_share))
            if size <= 0:
                return None
            fee = _fee(size)

        trade = Trade(
            mode=bot.mode,
            market_slug=market.slug,
            market_condition_id=market.condition_id,
            market_end_ts=market.end_unix or 0,
            token_id=token_id,
            outcome=signal.direction,
            side="BUY",
            size=size,
            entry_price=entry_price,
            fee=fee,
            status="OPEN",
            signal_type=signal.signal_type,
            signal_strength=signal.strength,
            btc_price_at_entry=signal.btc_price,
            btc_price_at_window_open=signal.btc_window_open_price or 0.0,
        )
        self._session.add(trade)
        await self._session.commit()
        await self._session.refresh(trade)
        return trade

    async def resolve_due_trades(self, market_lookup: dict[str, MarketInfo], btc_price_at_close: dict[int, float]) -> List[Trade]:
        """Resolve any OPEN trade whose market_end_ts has passed.

        For paper trades we use the live BTC price at close time (recorded by caller).
        Live trades will be resolved by polling Polymarket settle data — handled by
        a separate path.
        """
        from datetime import datetime as _dt

        now_unix = int(_dt.utcnow().timestamp())
        resolved: List[Trade] = []

        open_trades = await self.get_open_trades(mode="paper")
        for t in open_trades:
            if t.market_end_ts == 0:
                continue
            if t.market_end_ts > now_unix:
                continue
            # Determine outcome using BTC price recorded at the resolution time.
            # Fall back to entry price when window-open price is unknown so DOWN
            # trades aren't trivially resolved as LOSS (btc_close >= 0 is always true).
            btc_open = t.btc_price_at_window_open if t.btc_price_at_window_open > 0 else t.btc_price_at_entry
            btc_close = btc_price_at_close.get(t.market_end_ts, t.btc_price_at_entry)
            if btc_open <= 0:
                continue  # no reference price available — leave OPEN, retry later
            actual_direction = "UP" if btc_close >= btc_open else "DOWN"
            if t.outcome == actual_direction:
                t.status = "WIN"
                t.exit_price = 1.0
                t.pnl = (1.0 - t.entry_price) * t.size - t.fee
            else:
                t.status = "LOSS"
                t.exit_price = 0.0
                t.pnl = -t.entry_price * t.size - t.fee
            t.resolved_at = _dt.utcnow()
            self._session.add(t)
            resolved.append(t)

        if resolved:
            await self._session.commit()
        return resolved

    async def record_balance_snapshot(self, mode: str, market_lookup: dict[str, MarketInfo]) -> BalanceSnapshot:
        balance = await self.balance(mode)
        equity, unrealized = await self.equity(mode, market_lookup)
        open_count = len(await self.get_open_trades(mode))
        realized = await self.get_realized_pnl(mode)
        snap = BalanceSnapshot(
            mode=mode,
            balance=balance,
            equity=equity,
            open_positions=open_count,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
        )
        self._session.add(snap)
        await self._session.commit()
        await self._session.refresh(snap)
        return snap

    async def log_signal(self, signal: SignalOutput) -> SignalLog:
        log = SignalLog(
            market_condition_id=signal.market_condition_id,
            market_slug=signal.market_slug,
            market_end_unix=signal.market_end_unix,
            signal_type=signal.signal_type,
            direction=signal.direction,
            strength=signal.strength,
            edge=signal.edge,
            suggested_size=signal.suggested_size,
            btc_price=signal.btc_price,
            btc_window_open_price=signal.btc_window_open_price or 0.0,
            btc_pct_move=signal.btc_pct_move,
            token_up_price=signal.token_up_price,
            token_down_price=signal.token_down_price,
            note=signal.note,
        )
        self._session.add(log)
        await self._session.commit()
        await self._session.refresh(log)
        return log
