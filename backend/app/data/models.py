from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Field, SQLModel


class Trade(SQLModel, table=True):
    """A trade executed by the bot (paper or live)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
    resolved_at: Optional[datetime] = Field(default=None, index=True)

    mode: str = Field(default="paper", index=True)  # paper | live
    market_slug: str = Field(index=True)
    market_condition_id: str = Field(index=True)
    market_end_ts: int = Field(index=True)
    token_id: str
    outcome: str  # UP | DOWN
    side: str = Field(default="BUY")  # BUY | SELL
    size: float  # shares
    entry_price: float
    fee: float

    status: str = Field(default="OPEN", index=True)  # OPEN | WIN | LOSS | CLOSED
    exit_price: Optional[float] = None
    pnl: Optional[float] = None

    # Signal context
    signal_type: str = ""
    signal_strength: float = 0.0
    btc_price_at_entry: float = 0.0
    btc_price_at_window_open: float = 0.0


class BalanceSnapshot(SQLModel, table=True):
    """Time-series snapshots of bot balance for charting."""

    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=datetime.utcnow, index=True)
    mode: str = Field(default="paper", index=True)
    balance: float
    equity: float
    open_positions: int
    realized_pnl: float
    unrealized_pnl: float


class SignalLog(SQLModel, table=True):
    """Audit log of signals fired by the engine."""

    id: Optional[int] = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=datetime.utcnow, index=True)
    market_condition_id: str = Field(index=True)
    signal_type: str
    direction: str  # UP | DOWN | NEUTRAL
    strength: float
    btc_price: float
    token_up_price: float
    token_down_price: float
    note: str = ""


class BotSettings(SQLModel, table=True):
    """Singleton table for runtime tunables (single row, id=1)."""

    id: Optional[int] = Field(default=None, primary_key=True)
    mode: str = "paper"  # paper | live
    auto_trade: bool = False
    max_position_usd: float = 50.0
    max_concurrent: int = 3
    min_edge: float = 0.03
    dislocation_threshold: float = 0.002
    starting_balance: float = 1000.0
