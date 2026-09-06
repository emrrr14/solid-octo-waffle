"""Last-value cache: the only thing the valuation path is allowed to read.

Ticks go to two places with different jobs:

* Redis hash ``px:last`` - the *current* price, O(1), overwritten thousands of
  times a second.  This is what a page load and every 1 Hz valuation reads.
* TimescaleDB hypertable ``ticks`` - the *history*, written in batches by a
  separate consumer.  Never read it on the hot path.

Keeping them separate is what lets the write rate and the read rate scale
independently.
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from redis.asyncio import Redis

from app.domain.models import Tick

KEY_LAST = "px:last"        # hash: symbol -> "price:epoch_ms"
KEY_PREV_CLOSE = "px:prev_close"
CHANNEL_TICKS = "ticks"     # pub/sub fan-out to the websocket workers


@dataclass(frozen=True, slots=True)
class Quote:
    price: float
    ts_ms: int

    def is_stale(self, budget_ms: int = 5_000, now_ms: int | None = None) -> bool:
        return (now_ms or int(time.time() * 1000)) - self.ts_ms > budget_ms


class LastPriceCache:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._local: dict[str, Quote] = {}   # per-process L1, refreshed by pub/sub

    async def put(self, tick: Tick) -> None:
        ts_ms = int(tick.ts_event.timestamp() * 1000)
        prev = self._local.get(tick.symbol)
        if prev is not None and prev.ts_ms > ts_ms:
            return                            # out-of-order print; never regress a price
        quote = Quote(tick.price, ts_ms)
        self._local[tick.symbol] = quote
        await self._redis.hset(KEY_LAST, tick.symbol, f"{tick.price}:{ts_ms}")

    def get_local(self, symbol: str) -> Quote | None:
        return self._local.get(symbol)

    async def get_many(self, symbols: list[str]) -> dict[str, Quote]:
        missing = [s for s in symbols if s not in self._local]
        if missing:
            for symbol, raw in zip(missing, await self._redis.hmget(KEY_LAST, missing)):
                if raw:
                    price, ts = raw.decode().split(":")
                    self._local[symbol] = Quote(float(price), int(ts))
        return {s: self._local[s] for s in symbols if s in self._local}

    async def prev_closes(self, symbols: list[str]) -> dict[str, float]:
        """Yesterday's official close, written once by the EOD job."""
        raw = await self._redis.hmget(KEY_PREV_CLOSE, symbols)
        return {s: float(v) for s, v in zip(symbols, raw) if v}
