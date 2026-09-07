"""Single-runner locks for scheduled jobs.

Every scheduled job in this system is dangerous to run twice at once: two FED
pollers can both see the same step and both fire a rebalance; two ingest jobs
double the TEFAS request rate for no benefit.  Scaling the worker to two
replicas - or a rolling deploy where old and new overlap for thirty seconds -
is enough to cause it.

So each run takes a Redis lock with a TTL longer than the job's expected
duration.  The TTL matters more than the lock: a worker that dies mid-job must
not leave the schedule blocked forever, and a job that overruns its TTL is a bug
to fix rather than a case to handle by extending it indefinitely.

Release is compare-and-delete via Lua, so a job that overran cannot delete the
lock a *different* worker has since acquired.
"""
from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Protocol

log = logging.getLogger(__name__)

# Delete only if the value still matches the token we wrote.
RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else
  return 0
end
"""


class LockBackend(Protocol):
    async def acquire(self, key: str, token: str, ttl_ms: int) -> bool: ...
    async def release(self, key: str, token: str) -> None: ...


class RedisLockBackend:
    def __init__(self, redis) -> None:
        self._redis = redis

    async def acquire(self, key: str, token: str, ttl_ms: int) -> bool:
        return bool(await self._redis.set(key, token, nx=True, px=ttl_ms))

    async def release(self, key: str, token: str) -> None:
        await self._redis.eval(RELEASE_SCRIPT, 1, key, token)


class InMemoryLockBackend:
    """For a single-process deployment and for tests.  Honest about its limits:
    it protects against overlapping runs in *this* process only."""

    def __init__(self) -> None:
        self._held: dict[str, str] = {}

    async def acquire(self, key: str, token: str, ttl_ms: int) -> bool:
        if key in self._held:
            return False
        self._held[key] = token
        return True

    async def release(self, key: str, token: str) -> None:
        if self._held.get(key) == token:
            del self._held[key]


@asynccontextmanager
async def job_lock(backend: LockBackend, name: str, ttl_ms: int = 300_000) -> AsyncIterator[bool]:
    """``async with job_lock(...) as acquired:`` - skip the body when False."""
    token = uuid.uuid4().hex
    key = f"lock:job:{name}"
    acquired = await backend.acquire(key, token, ttl_ms)
    if not acquired:
        log.info("job %s: already running elsewhere, skipping this tick", name)
    try:
        yield acquired
    finally:
        if acquired:
            await backend.release(key, token)
