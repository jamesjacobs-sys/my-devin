from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class MarketInfo(BaseModel):
    condition_id: str
    slug: str
    question: str
    end_date_iso: Optional[str] = None
    end_unix: Optional[int] = None
    token_up_id: str
    token_down_id: str
    token_up_price: float
    token_down_price: float
    token_up_best_bid: Optional[float] = None
    token_up_best_ask: Optional[float] = None
    token_down_best_bid: Optional[float] = None
    token_down_best_ask: Optional[float] = None
    volume: float = 0.0
    liquidity: float = 0.0
    active: bool = True


class BTCTick(BaseModel):
    ts_ms: int
    bid: float
    ask: float
    mid: float
    source: str = "binance_spot"


class SignalOutput(BaseModel):
    ts: datetime
    market_condition_id: str
    market_slug: str
    market_end_unix: int
    signal_type: str
    direction: str
    strength: float
    edge: float
    suggested_size: float
    btc_price: float
    btc_window_open_price: Optional[float] = None
    btc_pct_move: float
    token_up_price: float
    token_down_price: float
    note: str = ""


class TradeRead(BaseModel):
    id: int
    created_at: datetime
    resolved_at: Optional[datetime]
    mode: str
    market_slug: str
    market_condition_id: str
    market_end_ts: int
    outcome: str
    size: float
    entry_price: float
    exit_price: Optional[float]
    fee: float
    pnl: Optional[float]
    status: str
    signal_type: str
    signal_strength: float
    btc_price_at_entry: float


class BalancePoint(BaseModel):
    ts: datetime
    balance: float
    equity: float
    open_positions: int
    realized_pnl: float
    unrealized_pnl: float


class BotStatus(BaseModel):
    mode: str
    auto_trade: bool
    paper_balance: float
    equity: float
    realized_pnl: float
    unrealized_pnl: float
    open_positions: int
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    btc_price: float
    btc_source: str
    connected_to_polymarket: bool
    connected_to_binance: bool
    active_markets: int


class SettingsUpdate(BaseModel):
    mode: Optional[str] = None
    auto_trade: Optional[bool] = None
    max_position_usd: Optional[float] = None
    max_concurrent: Optional[int] = None
    min_edge: Optional[float] = None
    dislocation_threshold: Optional[float] = None


class BacktestRequest(BaseModel):
    starting_balance: float = 1000.0
    minutes: int = 120
    min_edge: float = 0.03
    dislocation_threshold: float = 0.002
    max_position_usd: float = 50.0
    seed: int = 42


class BacktestTrade(BaseModel):
    window_start_unix: int
    window_end_unix: int
    direction: str
    entry_price: float
    exit_price: float
    size: float
    fee: float
    pnl: float
    btc_open: float
    btc_close: float


class BacktestResult(BaseModel):
    starting_balance: float
    ending_balance: float
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    roi_pct: float
    sharpe: float
    max_drawdown_pct: float
    trades: List[BacktestTrade]
    equity_curve: List[float]
    note: str = ""
