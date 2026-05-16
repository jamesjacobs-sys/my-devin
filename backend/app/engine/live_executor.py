from __future__ import annotations

"""Optional live execution adapter.

This module intentionally keeps a small surface — the rest of the system can run
in paper mode without `py-clob-client` installed. If a user later sets
`POLYMARKET_PRIVATE_KEY` and installs the SDK, this adapter is the only file that
needs to change to flip from paper to live trading.

Reference:
    https://github.com/Polymarket/py-clob-client
"""

import logging
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


def is_live_enabled() -> bool:
    return bool(settings.polymarket_private_key)


async def place_live_order(
    token_id: str, price: float, size: float, side: str = "BUY"
) -> Optional[dict]:
    """Place a real CLOB order. Returns the order response or None on error.

    The implementation is gated to keep paper-mode deployments lightweight.
    Activating live mode requires:

        pip install py-clob-client
        POLYMARKET_PRIVATE_KEY=0x...
    """
    if not is_live_enabled():
        logger.info("live mode not enabled; skipping live order")
        return None

    try:
        from py_clob_client.client import ClobClient  # type: ignore
        from py_clob_client.clob_types import OrderArgs  # type: ignore
        from py_clob_client.order_builder.constants import BUY, SELL  # type: ignore
    except ImportError:
        logger.error("py-clob-client not installed; cannot place live order")
        return None

    try:
        client = ClobClient(
            host=settings.polymarket_clob_host,
            key=settings.polymarket_private_key,
            chain_id=137,
        )
        creds = client.create_or_derive_api_creds()
        client.set_api_creds(creds)

        args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=BUY if side.upper() == "BUY" else SELL,
        )
        signed = client.create_order(args)
        return client.post_order(signed)
    except Exception as exc:
        logger.error("live order placement failed: %s", exc)
        return None
