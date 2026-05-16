from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

import numpy as np

from app.data.schemas import MarketInfo, SignalOutput

logger = logging.getLogger(__name__)


@dataclass
class MarketSnapshot:
    """A single market's CLOB + BTC state used for signal computation."""

    market: MarketInfo
    btc_price: float
    btc_window_open_price: Optional[float]
    btc_price_at_market_open: Optional[float]
    btc_history: List[Tuple[float, float]]  # (ts_ms, mid)


def _weighted_slope(history: List[Tuple[float, float]]) -> Tuple[float, float]:
    """Compute weighted regression slope across 30s/60s/120s/240s timeframes.

    Returns (weighted_slope_pct_per_sec, dispersion).
    """
    if len(history) < 8:
        return 0.0, 0.0

    now_ms = history[-1][0]
    timeframes = [(30, 0.4), (60, 0.3), (120, 0.2), (240, 0.1)]
    slopes = []
    weights = []
    for tf_sec, w in timeframes:
        window = [(t, p) for (t, p) in history if t >= now_ms - tf_sec * 1000]
        if len(window) < 4:
            continue
        xs = np.array([t / 1000.0 for (t, _) in window])
        ys = np.array([p for (_, p) in window])
        xs = xs - xs[0]
        if xs.std() < 1e-6:
            continue
        beta1, beta0 = np.polyfit(xs, ys, 1)
        mean_price = ys.mean()
        if mean_price == 0:
            continue
        slope_pct_per_sec = beta1 / mean_price
        slopes.append(slope_pct_per_sec)
        weights.append(w)

    if not slopes:
        return 0.0, 0.0

    weights_arr = np.array(weights) / sum(weights)
    weighted = float(np.dot(np.array(slopes), weights_arr))
    dispersion = float(np.std(slopes))
    return weighted, dispersion


def _fee_per_share(price: float) -> float:
    """Polymarket variable fee: price * 0.25 * (price*(1-price))^2."""
    if price <= 0 or price >= 1:
        return 0.0
    return price * 0.25 * (price * (1.0 - price)) ** 2


def evaluate_market(
    snap: MarketSnapshot,
    min_edge: float = 0.03,
    dislocation_threshold: float = 0.002,
) -> Optional[SignalOutput]:
    """Produce a SignalOutput if a tradable edge is detected, else None.

    Two signal families:

    1) **DISLOCATION** — BTC has moved by >= dislocation_threshold within the active
       5-min window but the token implied probability hasn't caught up.

    2) **MOMENTUM** — weighted multi-timeframe slope strongly suggests UP/DOWN with
       low dispersion across timeframes.
    """
    m = snap.market
    btc = snap.btc_price
    btc_open = snap.btc_window_open_price
    history = snap.btc_history

    if btc <= 0:
        return None

    pct_move = 0.0
    if btc_open and btc_open > 0:
        pct_move = (btc - btc_open) / btc_open

    weighted_slope, dispersion = _weighted_slope(history)

    direction = "NEUTRAL"
    signal_type = ""
    strength = 0.0

    if btc_open and abs(pct_move) >= dislocation_threshold:
        direction = "UP" if pct_move > 0 else "DOWN"
        signal_type = "DISLOCATION"
        strength = min(abs(pct_move) * 50.0, 1.0)  # 2% move => strength 1.0
    elif abs(weighted_slope) > 5e-6 and dispersion < abs(weighted_slope) * 1.2:
        direction = "UP" if weighted_slope > 0 else "DOWN"
        signal_type = "MOMENTUM"
        strength = float(min(abs(weighted_slope) * 2e5, 1.0))
    else:
        return None

    if direction == "UP":
        token_price = m.token_up_price
        token_ask = m.token_up_best_ask or m.token_up_price
    else:
        token_price = m.token_down_price
        token_ask = m.token_down_best_ask or m.token_down_price

    if token_ask <= 0 or token_ask >= 1:
        return None

    fee_rate = _fee_per_share(token_ask) + 0.02  # variable fee + taker
    implied_payoff = 1.0 - token_ask
    expected_value_pct = strength * implied_payoff - (1.0 - strength) * token_ask - fee_rate * token_ask
    edge = expected_value_pct / token_ask if token_ask > 0 else 0.0

    if edge < min_edge:
        return None

    suggested_size = min(strength * 50.0, 50.0)

    return SignalOutput(
        ts=datetime.utcnow(),
        market_condition_id=m.condition_id,
        market_slug=m.slug,
        market_end_unix=m.end_unix or 0,
        signal_type=signal_type,
        direction=direction,
        strength=strength,
        edge=edge,
        suggested_size=suggested_size,
        btc_price=btc,
        btc_window_open_price=btc_open,
        btc_pct_move=pct_move,
        token_up_price=m.token_up_price,
        token_down_price=m.token_down_price,
        note=f"weighted_slope={weighted_slope:.2e} dispersion={dispersion:.2e}",
    )
