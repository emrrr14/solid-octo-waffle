"""Upstream market-data adapters.

One rule governs this layer: **one upstream connection per venue per process,
never one per user.**  Vendors count concurrent connections and symbol
subscriptions, not customers; a socket per browser tab is how a retail feed bill
turns into an enterprise one and how you hit the vendor's connection cap at 200
users.  The gateway holds the single socket, normalises to :class:`Tick`, and
everything downstream fans out from Redis.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
from datetime import datetime, timezone
from typing import AsyncIterator, Protocol

import websockets

from app.domain.models import Tick, Venue

log = logging.getLogger(__name__)


class MarketDataProvider(Protocol):
    name: str

    def stream(self, symbols: list[str]) -> AsyncIterator[Tick]: ...


class FinnhubProvider:
    """US equities (NASDAQ / NYSE) over Finnhub's trade websocket.

    Swap for Polygon (``wss://socket.polygon.io/stocks``) or Alpaca without
    touching anything downstream - they all normalise to the same :class:`Tick`.
    """

    name = "finnhub"
    URL = "wss://ws.finnhub.io?token={token}"

    def __init__(self, token: str, venue: Venue = Venue.NASDAQ) -> None:
        self._token = token
        self._venue = venue

    async def stream(self, symbols: list[str]) -> AsyncIterator[Tick]:
        backoff = 1.0
        while True:
            try:
                async with websockets.connect(
                    self.URL.format(token=self._token),
                    ping_interval=20,      # detect half-open sockets
                    ping_timeout=10,
                    max_queue=1024,
                ) as ws:
                    for symbol in symbols:
                        await ws.send(json.dumps({"type": "subscribe", "symbol": symbol}))
                    log.info("finnhub: subscribed to %d symbols", len(symbols))
                    backoff = 1.0

                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") != "trade":
                            continue
                        now = datetime.now(timezone.utc)
                        for trade in msg.get("data", []):
                            yield Tick(
                                symbol=trade["s"],
                                price=float(trade["p"]),
                                ts_event=datetime.fromtimestamp(trade["t"] / 1000, tz=timezone.utc),
                                ts_ingest=now,
                                size=float(trade.get("v", 0) or 0),
                                venue=self._venue,
                            )
            except asyncio.CancelledError:
                raise
            except Exception:
                # Exponential backoff with jitter: a vendor outage must not turn
                # into a reconnect storm from every one of our pods.
                sleep_for = min(backoff, 30.0) * (0.5 + random.random())
                log.exception("finnhub: stream dropped, reconnecting in %.1fs", sleep_for)
                await asyncio.sleep(sleep_for)
                backoff = min(backoff * 2, 30.0)


class PollingProvider:
    """Fallback for venues without a websocket (most BIST retail feeds).

    Polls a REST snapshot endpoint and emits ticks only when the price actually
    changed, so downstream code cannot tell the difference apart from the
    resolution.  ``interval`` must respect the vendor's rate limit.
    """

    name = "polling"

    def __init__(self, fetch_quotes, venue: Venue, interval: float = 1.0) -> None:
        self._fetch = fetch_quotes          # async (symbols) -> {symbol: (price, ts_event)}
        self._venue = venue
        self._interval = interval

    async def stream(self, symbols: list[str]) -> AsyncIterator[Tick]:
        last: dict[str, float] = {}
        while True:
            started = asyncio.get_running_loop().time()
            try:
                quotes = await self._fetch(symbols)
                now = datetime.now(timezone.utc)
                for symbol, (price, ts_event) in quotes.items():
                    if last.get(symbol) == price:
                        continue
                    last[symbol] = price
                    yield Tick(symbol, float(price), ts_event, now, venue=self._venue)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("%s: poll failed", self._venue)
            elapsed = asyncio.get_running_loop().time() - started
            await asyncio.sleep(max(0.0, self._interval - elapsed))
