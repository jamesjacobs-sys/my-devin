from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

import httpx
import websockets

from app.core.config import settings
from app.data.schemas import MarketInfo

logger = logging.getLogger(__name__)


class PolymarketClient:
    """Async client wrapping the public Polymarket Gamma + CLOB REST APIs."""

    def __init__(self) -> None:
        self._client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)

    async def close(self) -> None:
        await self._client.aclose()

    async def fetch_active_5m_btc_markets(self, limit: int = 40) -> List[MarketInfo]:
        """Discover live BTC 5-minute markets.

        Strategy:
        1. Probe by deterministic slug `btc-updown-5m-{unix}` for the next N 5-min
           windows. Polymarket's general listing endpoint filters out 5m markets in
           odd ways, but per-slug lookup works reliably.
        2. Also pull recent events with `tag_slug=crypto-prices` as a backup.
        """
        markets: Dict[str, MarketInfo] = {}

        # Probe upcoming 5-minute boundaries.
        now_unix = int(time.time())
        # Round UP to next 5-min boundary.
        next_boundary = now_unix - (now_unix % 300) + 300
        targets = [next_boundary + i * 300 for i in range(-1, 9)]  # 1 past + 9 future

        async def _probe(ts: int) -> None:
            slug = f"btc-updown-5m-{ts}"
            try:
                resp = await self._client.get(
                    f"{settings.polymarket_gamma_host}/markets",
                    params={"slug": slug},
                )
                if resp.status_code != 200:
                    return
                data = resp.json()
                if not data:
                    return
                info = self._parse_gamma_market(data[0])
                if info and info.active:
                    markets[info.condition_id or slug] = info
            except Exception as exc:
                logger.debug("probe %s failed: %s", slug, exc)

        await asyncio.gather(*[_probe(ts) for ts in targets])

        if len(markets) < 3:
            # Backup: scan events listing for crypto-prices tag.
            try:
                resp = await self._client.get(
                    f"{settings.polymarket_gamma_host}/events",
                    params={
                        "tag_slug": "crypto-prices",
                        "closed": "false",
                        "limit": "200",
                        "order": "endDate",
                        "ascending": "true",
                    },
                )
                if resp.status_code == 200:
                    for e in resp.json():
                        es = e.get("slug", "") or ""
                        if not es.startswith("btc-updown-5m"):
                            continue
                        for m in e.get("markets") or []:
                            info = self._parse_gamma_market(m)
                            if info and info.active and info.end_unix and info.end_unix >= now_unix - 60:
                                markets[info.condition_id or info.slug] = info
            except Exception as exc:
                logger.debug("events backup failed: %s", exc)

        result = sorted(markets.values(), key=lambda x: x.end_unix or 0)[:limit]
        return result

    def _parse_gamma_market(self, m: Dict[str, Any]) -> Optional[MarketInfo]:
        try:
            slug = m.get("slug") or ""
            condition_id = m.get("conditionId") or m.get("condition_id") or ""
            question = m.get("question") or ""
            end_iso = m.get("endDate")
            end_unix = self._slug_to_unix(slug) or self._iso_to_unix(end_iso)

            tokens = m.get("clobTokenIds")
            if isinstance(tokens, str):
                tokens = json.loads(tokens)
            if not tokens or len(tokens) != 2:
                return None
            outcome_prices = m.get("outcomePrices")
            if isinstance(outcome_prices, str):
                outcome_prices = json.loads(outcome_prices)
            up_price = float(outcome_prices[0]) if outcome_prices else 0.5
            down_price = float(outcome_prices[1]) if outcome_prices else 0.5

            return MarketInfo(
                condition_id=condition_id,
                slug=slug,
                question=question,
                end_date_iso=end_iso,
                end_unix=end_unix,
                token_up_id=str(tokens[0]),
                token_down_id=str(tokens[1]),
                token_up_price=up_price,
                token_down_price=down_price,
                volume=float(m.get("volume") or 0.0),
                liquidity=float(m.get("liquidity") or 0.0),
                active=bool(m.get("active", True)),
            )
        except Exception as exc:
            logger.debug("parse_gamma_market failed: %s", exc)
            return None

    @staticmethod
    def _slug_to_unix(slug: str) -> Optional[int]:
        if not slug.startswith("btc-updown-5m-"):
            return None
        try:
            return int(slug.rsplit("-", 1)[-1])
        except ValueError:
            return None

    @staticmethod
    def _iso_to_unix(iso: Optional[str]) -> Optional[int]:
        if not iso:
            return None
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return int(dt.replace(tzinfo=timezone.utc).timestamp() if dt.tzinfo is None else dt.timestamp())
        except Exception:
            return None

    async def _fallback_clob_scan(self, limit: int) -> List[MarketInfo]:
        try:
            resp = await self._client.get(f"{settings.polymarket_clob_host}/markets")
            if resp.status_code != 200:
                return []
            data = resp.json().get("data", [])
            result: List[MarketInfo] = []
            for m in data[:limit]:
                slug = m.get("market_slug") or ""
                if not slug.startswith("btc-updown-5m"):
                    continue
                tokens = m.get("tokens") or []
                if len(tokens) != 2:
                    continue
                result.append(
                    MarketInfo(
                        condition_id=m.get("condition_id") or "",
                        slug=slug,
                        question=m.get("question") or "",
                        end_date_iso=m.get("end_date_iso"),
                        end_unix=self._slug_to_unix(slug),
                        token_up_id=str(tokens[0].get("token_id")),
                        token_down_id=str(tokens[1].get("token_id")),
                        token_up_price=float(tokens[0].get("price") or 0.5),
                        token_down_price=float(tokens[1].get("price") or 0.5),
                        active=bool(m.get("active", True)),
                    )
                )
            return result
        except Exception as exc:
            logger.warning("CLOB fallback failed: %s", exc)
            return []

    async def fetch_orderbook(self, token_id: str) -> Optional[Dict[str, Any]]:
        try:
            resp = await self._client.get(
                f"{settings.polymarket_clob_host}/book", params={"token_id": token_id}
            )
            if resp.status_code == 200:
                return resp.json()
        except Exception as exc:
            logger.debug("orderbook fetch failed: %s", exc)
        return None

    async def fetch_midpoint(self, token_id: str) -> Optional[float]:
        try:
            resp = await self._client.get(
                f"{settings.polymarket_clob_host}/midpoint",
                params={"token_id": token_id},
            )
            if resp.status_code == 200:
                v = resp.json().get("mid")
                if v is not None:
                    return float(v)
        except Exception:
            return None
        return None


class PolymarketMarketWS:
    """Subscribes to Polymarket CLOB market WS for one or more asset_ids."""

    def __init__(self, on_event: Callable[[Dict[str, Any]], None]) -> None:
        self._on_event = on_event
        self._task: Optional[asyncio.Task] = None
        self._assets: List[str] = []
        self._connected = False
        self._stop = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def start(self, asset_ids: List[str]) -> None:
        self._assets = asset_ids
        self._stop = False
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def update_assets(self, asset_ids: List[str]) -> None:
        self._assets = asset_ids
        # WS server doesn't support dynamic resubscribe reliably for market channel;
        # easiest: restart the connection. The reconnect loop will pick up new IDs.
        if self._task:
            self._task.cancel()
        await asyncio.sleep(0.1)
        await self.start(asset_ids)

    async def stop(self) -> None:
        self._stop = True
        if self._task:
            self._task.cancel()

    async def _run(self) -> None:
        backoff = 1.0
        while not self._stop:
            if not self._assets:
                await asyncio.sleep(2.0)
                continue
            try:
                async with websockets.connect(settings.polymarket_clob_ws, ping_interval=None) as ws:
                    self._connected = True
                    sub = {
                        "assets_ids": self._assets[:500],
                        "type": "market",
                        "custom_feature_enabled": True,
                    }
                    await ws.send(json.dumps(sub))
                    backoff = 1.0

                    async def _ping() -> None:
                        try:
                            while True:
                                await asyncio.sleep(10)
                                await ws.send("PING")
                        except Exception:
                            return

                    pinger = asyncio.create_task(_ping())
                    try:
                        async for raw in ws:
                            if raw == "PONG":
                                continue
                            try:
                                data = json.loads(raw)
                            except json.JSONDecodeError:
                                continue
                            if isinstance(data, list):
                                for ev in data:
                                    self._on_event(ev)
                            elif isinstance(data, dict):
                                self._on_event(data)
                    finally:
                        pinger.cancel()
            except asyncio.CancelledError:
                self._connected = False
                raise
            except Exception as exc:
                self._connected = False
                logger.warning("Polymarket WS disconnect: %s", exc)
                await asyncio.sleep(min(backoff, 30.0))
                backoff = min(backoff * 2, 30.0)
            finally:
                self._connected = False
