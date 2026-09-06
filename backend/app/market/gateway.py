"""Market-data gateway: the single process that owns upstream connections.

    [vendor WS] --> gateway --> Redis  px:last   (last-value cache, hot reads)
                            --> Redis  ticks     (pub/sub fan-out to WS workers)
                            --> queue  -> batch writer -> TimescaleDB (history)

Deploy it as its own service with **replica count 1 per venue** (or leader
election).  Two replicas subscribing to the same symbols double the vendor bill
and publish duplicate ticks.  The API pods, by contrast, scale horizontally -
they only read Redis.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from redis.asyncio import Redis

from app.domain.models import Tick
from app.market.cache import CHANNEL_TICKS, LastPriceCache
from app.market.providers import MarketDataProvider

log = logging.getLogger(__name__)


@dataclass(slots=True)
class GatewayStats:
    ticks: int = 0
    dropped: int = 0
    last_latency_ms: float = 0.0


class MarketGateway:
    def __init__(
        self,
        redis: Redis,
        cache: LastPriceCache,
        history_queue: asyncio.Queue[Tick] | None = None,
    ) -> None:
        self._redis = redis
        self._cache = cache
        self._history: asyncio.Queue[Tick] | None = history_queue
        self.stats = GatewayStats()

    async def run(self, provider: MarketDataProvider, symbols: list[str]) -> None:
        log.info("gateway: starting %s for %d symbols", provider.name, len(symbols))
        async for tick in provider.stream(symbols):
            await self._cache.put(tick)
            # Fan-out payload is deliberately tiny: symbol|price|ts.  The
            # websocket workers do the portfolio maths; the bus carries prices.
            await self._redis.publish(
                CHANNEL_TICKS, f"{tick.symbol}|{tick.price}|{int(tick.ts_event.timestamp()*1000)}"
            )
            self.stats.ticks += 1
            self.stats.last_latency_ms = tick.latency_ms

            if self._history is not None:
                try:
                    self._history.put_nowait(tick)
                except asyncio.QueueFull:
                    # History is best-effort; never let the archive path stall
                    # the live path.  The drop counter is an alerting signal.
                    self.stats.dropped += 1


async def history_writer(
    queue: asyncio.Queue[Tick],
    insert_batch,                      # async (list[Tick]) -> None
    batch_size: int = 500,
    flush_interval: float = 1.0,
) -> None:
    """Batch ticks into TimescaleDB.  Row-at-a-time inserts will not keep up."""
    batch: list[Tick] = []
    while True:
        try:
            tick = await asyncio.wait_for(queue.get(), timeout=flush_interval)
            batch.append(tick)
        except asyncio.TimeoutError:
            pass
        if batch and (len(batch) >= batch_size or queue.empty()):
            try:
                await insert_batch(batch)
            except Exception:
                log.exception("history writer: batch of %d failed", len(batch))
            batch = []
