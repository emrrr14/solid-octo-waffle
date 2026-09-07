"""Worker entry point.

    DEV_MODE=1 python -m app.worker.main

Deploy it separately from the API: it is long-running, CPU-bursty (LP solves)
and must not compete with request latency.  One replica is enough - the locks
exist so that a rolling deploy overlapping two replicas is safe, not so you can
scale it out.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

import httpx
from redis.asyncio import Redis

from app.db.repositories import DecisionRepository, PortfolioRepository
from app.db.session import create_engine, create_session_factory
from app.macro.fed import FedRateMonitor, FredClient, InMemoryRateStateStore
from app.macro.triggers import TriggerRouter
from app.settings import load_settings
from app.tefas.client import TefasClient
from app.worker.jobs import WorkerContext
from app.worker.lock import InMemoryLockBackend, RedisLockBackend
from app.worker.scheduler import build_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("worker")


async def run() -> None:
    settings = load_settings()
    engine = create_engine(settings.database_url)
    factory = create_session_factory(engine)

    http = httpx.AsyncClient(timeout=20.0)
    redis: Redis | None = None
    locks = InMemoryLockBackend()
    try:
        redis = Redis.from_url(settings.redis_url)
        await redis.ping()
        locks = RedisLockBackend(redis)
        log.info("worker: using Redis locks")
    except Exception:
        # Single-process development. Said out loud, because with in-memory
        # locks a second replica would happily run everything twice.
        log.warning("worker: Redis unavailable, falling back to in-process locks")

    fed = None
    if settings.fred_api_key:
        fed = FedRateMonitor(
            FredClient(settings.fred_api_key, http),
            # Swap for a Postgres-backed store before running more than one
            # worker: an in-memory watermark re-fires on every restart.
            InMemoryRateStateStore(),
        )
    else:
        log.warning("worker: FRED_API_KEY unset - the FED poller is disabled")

    ctx = WorkerContext(
        portfolios=PortfolioRepository(factory),
        decisions=DecisionRepository(factory),
        session_factory=factory,
        router=TriggerRouter(),
        fed=fed,
        tefas=TefasClient(http),
    )

    scheduler = build_scheduler(ctx, locks)
    scheduler.start()
    for job in scheduler.get_jobs():
        log.info("scheduled %s -> next run %s", job.name, job.next_run_time)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    try:
        await stop.wait()
    finally:
        log.info("worker: shutting down")
        # wait=True: let an in-flight rebalance finish and persist its decision
        # rather than dying between the LP solve and the database write.
        scheduler.shutdown(wait=True)
        await http.aclose()
        if redis is not None:
            await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
