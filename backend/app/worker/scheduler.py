"""When the jobs run.

Schedules, and why each is what it is:

* **FED poll, every 15 minutes.**  FRED publishes once a day, but the FOMC
  statement lands at 18:00 UTC on decision days and the value appears within
  minutes.  Quarter-hourly polling costs nothing and bounds the reaction delay;
  for sub-minute reaction, add a decision-day webhook and keep this as the
  backstop.
* **TEFAS ingest, 20:30 Europe/Istanbul.**  NAVs are struck after the close and
  published during the evening.  Running at 18:00 fetches yesterday's numbers
  and looks like a data outage.
* **Nightly refit is *not* scheduled here.**  Betas are estimated inside the
  rebalance job, on the panel as it stands at decision time, so a decision can
  never be made on a model fitted against data that has since been revised.

Every job is wrapped identically: a distributed lock (two workers must not both
fire a rebalance), `max_instances=1`, `coalesce=True` so a paused laptop does not
replay six missed ticks at once, and a grace time after which a missed run is
skipped rather than run late against stale inputs.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.worker.jobs import WorkerContext, ingest_tefas, poll_fed
from app.worker.lock import LockBackend, job_lock

log = logging.getLogger(__name__)

TIMEZONE = "Europe/Istanbul"


def guarded(
    name: str,
    job: Callable[[WorkerContext], Awaitable[Any]],
    ctx: WorkerContext,
    locks: LockBackend,
    ttl_ms: int,
) -> Callable[[], Awaitable[None]]:
    """Wrap a job with the lock, timing and blanket error handling.

    A scheduled job that raises must never take the scheduler down with it - the
    next tick has to happen regardless, and the traceback belongs in the log, not
    in a dead process.
    """

    async def run() -> None:
        async with job_lock(locks, name, ttl_ms) as acquired:
            if not acquired:
                return
            started = time.monotonic()
            try:
                result = await job(ctx)
                log.info("job %s finished in %.1fs -> %s", name, time.monotonic() - started, result)
            except Exception:
                log.exception("job %s failed after %.1fs", name, time.monotonic() - started)

    return run


def build_scheduler(
    ctx: WorkerContext,
    locks: LockBackend,
    fed_interval_minutes: int = 15,
    tefas_hour: int = 20,
    tefas_minute: int = 30,
) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(
        timezone=TIMEZONE,
        job_defaults={
            "coalesce": True,      # one catch-up run, not six
            "max_instances": 1,    # never overlap a job with itself
            "misfire_grace_time": 300,
        },
    )

    scheduler.add_job(
        guarded("poll_fed", poll_fed, ctx, locks, ttl_ms=120_000),
        IntervalTrigger(minutes=fed_interval_minutes, jitter=30),
        id="poll_fed",
        name="FED policy-rate poll",
    )

    scheduler.add_job(
        guarded("ingest_tefas", ingest_tefas, ctx, locks, ttl_ms=900_000),
        CronTrigger(hour=tefas_hour, minute=tefas_minute, timezone=TIMEZONE),
        id="ingest_tefas",
        name="TEFAS/BES end-of-day ingest",
    )

    return scheduler
