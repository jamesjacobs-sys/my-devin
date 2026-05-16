from __future__ import annotations

import math
from typing import List

import numpy as np

from app.data.schemas import BacktestRequest, BacktestResult, BacktestTrade


def _fee_per_share(price: float) -> float:
    if price <= 0 or price >= 1:
        return 0.0
    return price * 0.25 * (price * (1.0 - price)) ** 2


def run_backtest(req: BacktestRequest, btc_series: List[float]) -> BacktestResult:
    """Backtest the dislocation strategy on a BTC price series.

    `btc_series` is interpreted as 1-second mid prices. The simulator slices it
    into 5-minute windows, evaluates a token price that lags real momentum, and
    runs the strategy with the requested parameters.
    """
    rng = np.random.default_rng(req.seed)
    n_windows = max(1, len(btc_series) // 300)
    trades: List[BacktestTrade] = []
    balance = req.starting_balance
    equity_curve = [balance]
    peak = balance
    max_dd = 0.0

    for i in range(n_windows):
        start = i * 300
        end = start + 300
        window = btc_series[start:end]
        if len(window) < 30:
            continue
        btc_open = window[0]
        btc_close = window[-1]
        # mid-window snapshot for the decision
        mid_index = 90  # decide ~30% into window
        if mid_index >= len(window):
            continue
        btc_mid = window[mid_index]
        pct_move = (btc_mid - btc_open) / btc_open if btc_open else 0.0

        if abs(pct_move) < req.dislocation_threshold:
            equity_curve.append(balance)
            continue

        direction = "UP" if pct_move > 0 else "DOWN"

        # Simulated token price: it tracks BTC pct move with lag + noise.
        # Token implied probability anchored at 0.5 when window opens.
        true_prob = 1 / (1 + math.exp(-pct_move * 200))
        lag = 0.4 + rng.uniform(0, 0.3)  # token reflects 40-70% of the move
        token_implied = 0.5 + (true_prob - 0.5) * lag
        token_implied = float(np.clip(token_implied, 0.05, 0.95))

        # Entry: buy the side aligned with BTC move
        entry_price = token_implied if direction == "UP" else 1 - token_implied
        # Add small slippage
        entry_price = float(np.clip(entry_price + rng.uniform(0.005, 0.02), 0.05, 0.95))

        edge = (1.0 - entry_price) * true_prob - entry_price * (1 - true_prob)
        edge_pct = edge / max(entry_price, 1e-6)
        if edge_pct < req.min_edge:
            equity_curve.append(balance)
            continue

        size_usd = min(req.max_position_usd, balance * 0.05)
        if size_usd <= 0:
            equity_curve.append(balance)
            continue
        size_shares = size_usd / entry_price

        fee = _fee_per_share(entry_price) * size_shares + entry_price * size_shares * 0.02

        actual = "UP" if btc_close >= btc_open else "DOWN"
        if direction == actual:
            pnl = (1.0 - entry_price) * size_shares - fee
            exit_price = 1.0
        else:
            pnl = -entry_price * size_shares - fee
            exit_price = 0.0

        balance += pnl
        peak = max(peak, balance)
        if peak > 0:
            dd = (peak - balance) / peak
            max_dd = max(max_dd, dd)
        equity_curve.append(balance)

        trades.append(
            BacktestTrade(
                window_start_unix=start,
                window_end_unix=end,
                direction=direction,
                entry_price=entry_price,
                exit_price=exit_price,
                size=size_shares,
                fee=fee,
                pnl=pnl,
                btc_open=btc_open,
                btc_close=btc_close,
            )
        )

    wins = sum(1 for t in trades if t.pnl > 0)
    losses = len(trades) - wins
    win_rate = (wins / len(trades)) if trades else 0.0
    roi = ((balance - req.starting_balance) / req.starting_balance * 100) if req.starting_balance else 0.0
    pnls = np.array([t.pnl for t in trades], dtype=float)
    sharpe = float(pnls.mean() / pnls.std() * math.sqrt(len(trades))) if len(trades) > 1 and pnls.std() > 1e-9 else 0.0

    return BacktestResult(
        starting_balance=req.starting_balance,
        ending_balance=balance,
        total_trades=len(trades),
        wins=wins,
        losses=losses,
        win_rate=win_rate,
        roi_pct=roi,
        sharpe=sharpe,
        max_drawdown_pct=max_dd * 100,
        trades=trades,
        equity_curve=equity_curve,
        note=(
            "Backtest run on the supplied BTC price series with simulated token "
            "implied probability that lags real BTC moves by 40-70%."
        ),
    )


def generate_synthetic_btc_series(minutes: int = 120, start_price: float = 65000.0, seed: int = 42) -> List[float]:
    """Generate a realistic BTC mid price series at 1-second resolution.

    The model is geometric Brownian motion + occasional jumps, calibrated to
    typical 1-minute BTC volatility (~0.1%).
    """
    rng = np.random.default_rng(seed)
    n = minutes * 60
    drift = 0.0
    sigma_per_sec = 0.10 / 100 / math.sqrt(60)  # ~0.10% per minute => per-second sigma
    prices = [start_price]
    for _ in range(n - 1):
        # Add occasional jumps
        jump = 0.0
        if rng.random() < 0.0005:
            jump = rng.normal(0.0, 0.003)
        shock = rng.normal(drift, sigma_per_sec) + jump
        prices.append(prices[-1] * math.exp(shock))
    return prices
