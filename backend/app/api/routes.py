from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import List

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.data.models import BalanceSnapshot, BotSettings, SignalLog, Trade
from app.data.schemas import (
    BacktestRequest,
    BacktestResult,
    BalancePoint,
    BotStatus,
    MarketInfo,
    SettingsUpdate,
    SignalOutput,
    TradeRead,
)
from app.engine.backtest import generate_synthetic_btc_series, run_backtest
from app.engine.orchestrator import orchestrator
from app.engine.trader import PaperTrader

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/status", response_model=BotStatus)
async def get_status(session: AsyncSession = Depends(get_session)) -> BotStatus:
    trader = PaperTrader(session)
    bot = await trader.get_settings()
    balance = await trader.balance(bot.mode)
    equity, unrealized = await trader.equity(bot.mode, orchestrator.markets)
    realized = await trader.get_realized_pnl(bot.mode)
    open_trades = await trader.get_open_trades(bot.mode)

    res = await session.execute(select(Trade).where(Trade.mode == bot.mode, Trade.status != "OPEN"))
    closed = list(res.scalars())
    wins = sum(1 for t in closed if t.status == "WIN")
    losses = sum(1 for t in closed if t.status == "LOSS")
    total = wins + losses
    win_rate = wins / total if total else 0.0

    btc_latest = orchestrator.btc.latest
    btc_price = btc_latest[1] if btc_latest else 0.0

    return BotStatus(
        mode=bot.mode,
        auto_trade=bot.auto_trade,
        paper_balance=balance,
        equity=equity,
        realized_pnl=realized,
        unrealized_pnl=unrealized,
        open_positions=len(open_trades),
        total_trades=total,
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        btc_price=btc_price,
        btc_source=orchestrator.btc.source,
        connected_to_polymarket=orchestrator.market_ws.connected,
        connected_to_binance=orchestrator.btc.connected,
        active_markets=len(orchestrator.markets),
    )


@router.get("/markets", response_model=List[MarketInfo])
async def get_markets() -> List[MarketInfo]:
    return list(orchestrator.markets.values())


@router.get("/btc/price")
async def get_btc_price() -> dict:
    latest = orchestrator.btc.latest
    history = orchestrator.btc.get_history()
    return {
        "latest": {
            "ts_ms": latest[0] if latest else None,
            "bid": latest[1] if latest else None,
            "ask": latest[2] if latest else None,
            "mid": (latest[1] + latest[2]) / 2.0 if latest else None,
        },
        "history": [
            {"ts_ms": ts, "mid": p} for (ts, p) in history[-600:]  # last 10 minutes
        ],
        "source": orchestrator.btc.source,
        "connected": orchestrator.btc.connected,
    }


@router.get("/trades", response_model=List[TradeRead])
async def get_trades(limit: int = 100, mode: str = "paper", session: AsyncSession = Depends(get_session)):
    stmt = select(Trade).where(Trade.mode == mode).order_by(Trade.created_at.desc()).limit(limit)
    res = await session.execute(stmt)
    rows = list(res.scalars())
    return [
        TradeRead(
            id=r.id,
            created_at=r.created_at,
            resolved_at=r.resolved_at,
            mode=r.mode,
            market_slug=r.market_slug,
            market_condition_id=r.market_condition_id,
            market_end_ts=r.market_end_ts,
            outcome=r.outcome,
            size=r.size,
            entry_price=r.entry_price,
            exit_price=r.exit_price,
            fee=r.fee,
            pnl=r.pnl,
            status=r.status,
            signal_type=r.signal_type,
            signal_strength=r.signal_strength,
            btc_price_at_entry=r.btc_price_at_entry,
        )
        for r in rows
    ]


@router.get("/balance/history", response_model=List[BalancePoint])
async def get_balance_history(
    limit: int = 500, mode: str = "paper", session: AsyncSession = Depends(get_session)
):
    stmt = (
        select(BalanceSnapshot)
        .where(BalanceSnapshot.mode == mode)
        .order_by(BalanceSnapshot.ts.desc())
        .limit(limit)
    )
    res = await session.execute(stmt)
    rows = list(res.scalars())
    rows.reverse()
    return [
        BalancePoint(
            ts=r.ts,
            balance=r.balance,
            equity=r.equity,
            open_positions=r.open_positions,
            realized_pnl=r.realized_pnl,
            unrealized_pnl=r.unrealized_pnl,
        )
        for r in rows
    ]


@router.get("/signals", response_model=List[SignalOutput])
async def get_signals(limit: int = 100, session: AsyncSession = Depends(get_session)):
    stmt = select(SignalLog).order_by(SignalLog.ts.desc()).limit(limit)
    res = await session.execute(stmt)
    rows = list(res.scalars())
    out: List[SignalOutput] = []
    for r in rows:
        out.append(
            SignalOutput(
                ts=r.ts,
                market_condition_id=r.market_condition_id,
                market_slug=r.market_slug or "",
                market_end_unix=r.market_end_unix or 0,
                signal_type=r.signal_type,
                direction=r.direction,
                strength=r.strength,
                edge=r.edge or 0.0,
                suggested_size=r.suggested_size or 0.0,
                btc_price=r.btc_price,
                btc_window_open_price=r.btc_window_open_price or 0.0,
                btc_pct_move=r.btc_pct_move or 0.0,
                token_up_price=r.token_up_price,
                token_down_price=r.token_down_price,
                note=r.note,
            )
        )
    return out


@router.get("/settings")
async def get_settings_endpoint(session: AsyncSession = Depends(get_session)):
    trader = PaperTrader(session)
    bot = await trader.get_settings()
    return {
        "mode": bot.mode,
        "auto_trade": bot.auto_trade,
        "max_position_usd": bot.max_position_usd,
        "max_concurrent": bot.max_concurrent,
        "min_edge": bot.min_edge,
        "dislocation_threshold": bot.dislocation_threshold,
        "starting_balance": bot.starting_balance,
        "live_credentials_present": bool(settings.polymarket_private_key),
    }


@router.post("/settings")
async def update_settings(
    update: SettingsUpdate, session: AsyncSession = Depends(get_session)
):
    trader = PaperTrader(session)
    bot = await trader.get_settings()
    if update.mode is not None:
        bot.mode = update.mode
    if update.auto_trade is not None:
        bot.auto_trade = update.auto_trade
    if update.max_position_usd is not None:
        bot.max_position_usd = update.max_position_usd
    if update.max_concurrent is not None:
        bot.max_concurrent = update.max_concurrent
    if update.min_edge is not None:
        bot.min_edge = update.min_edge
    if update.dislocation_threshold is not None:
        bot.dislocation_threshold = update.dislocation_threshold
    session.add(bot)
    await session.commit()
    await session.refresh(bot)
    return {"ok": True}


@router.post("/reset")
async def reset_paper(session: AsyncSession = Depends(get_session)):
    from sqlalchemy import delete

    await session.execute(delete(Trade).where(Trade.mode == "paper"))
    await session.execute(delete(BalanceSnapshot).where(BalanceSnapshot.mode == "paper"))
    await session.commit()
    return {"ok": True}


@router.post("/backtest", response_model=BacktestResult)
async def backtest(req: BacktestRequest):
    series = generate_synthetic_btc_series(
        minutes=max(5, min(req.minutes, 1440)), seed=req.seed
    )
    return run_backtest(req, series)


@router.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    q = orchestrator.subscribe()
    try:
        # Send a snapshot first
        await ws.send_text(
            json.dumps(
                {
                    "type": "snapshot",
                    "markets": [m.model_dump() for m in orchestrator.markets.values()],
                    "btc": {
                        "mid": orchestrator.btc.mid,
                        "connected": orchestrator.btc.connected,
                    },
                    "ts": datetime.utcnow().isoformat(),
                }
            )
        )
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=15.0)
                await ws.send_text(json.dumps(event, default=str))
            except asyncio.TimeoutError:
                # send heartbeat
                btc = orchestrator.btc.latest
                await ws.send_text(
                    json.dumps(
                        {
                            "type": "heartbeat",
                            "btc": {"mid": (btc[1] + btc[2]) / 2.0 if btc else None},
                            "polymarket_connected": orchestrator.market_ws.connected,
                            "binance_connected": orchestrator.btc.connected,
                            "ts": datetime.utcnow().isoformat(),
                        }
                    )
                )
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("ws error: %s", exc)
    finally:
        orchestrator.unsubscribe(q)
