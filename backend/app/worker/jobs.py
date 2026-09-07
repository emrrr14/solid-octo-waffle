"""The scheduled jobs.

Each is a plain coroutine taking a :class:`WorkerContext` - no scheduler types,
no globals - so every one of them can be driven directly from a test or a
one-off script.  The scheduler in `scheduler.py` only decides *when*.

    poll_fed          every 15 min   FRED step -> macro event -> maybe rebalance
    ingest_tefas      20:30 TRT      TEFAS/BES NAVs for the trailing window
    rebalance_all     on trigger     one decision per portfolio
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from app.analytics.regression import fit_panel
from app.analytics.panel import build_panel
from app.db.repositories import DecisionRepository, PortfolioRepository
from app.domain.models import RebalanceTrigger
from app.macro.fed import FedRateMonitor
from app.macro.triggers import TriggerRouter
from app.rebalance.engine import RebalanceEngine
from app.rebalance.mandate import MandateError, parse_mandate
from app.tefas.client import TefasClient

log = logging.getLogger(__name__)

# Two years of daily observations: long enough to see several FOMC cycles,
# short enough that the estimated betas still describe the current regime.
DEFAULT_LOOKBACK_DAYS = 750
MIN_OBSERVATIONS = 120


@dataclass(slots=True)
class WorkerContext:
    portfolios: PortfolioRepository
    decisions: DecisionRepository
    session_factory: object
    router: TriggerRouter
    fed: FedRateMonitor | None = None
    tefas: TefasClient | None = None
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    tefas_window_days: int = 7
    fund_types: tuple[str, ...] = ("YAT", "EMK")
    expected_rate_pct: float | None = None   # market-implied, from fed funds futures


async def poll_fed(ctx: WorkerContext) -> str | None:
    """Detect a policy step, record it, and rebalance if it is material.

    Returns the macro event id when one was recorded, else ``None``.

    Ordering matters: the event is written *before* any rebalance, and the write
    is deduplicated by ``(series, observed_on, new_value)``.  A FRED
    re-publication of the same decision therefore returns ``None`` here and
    cannot replay a rebalance - which is the whole point of persisting it.
    """
    if ctx.fed is None:
        log.debug("poll_fed: no FRED client configured, skipping")
        return None

    event = await ctx.fed.poll(expected_rate_pct=ctx.expected_rate_pct)
    if event is None:
        return None

    trigger = ctx.router.evaluate(event)
    note = "" if trigger else "Piyasada fiyatlanmıştı, işlem yapılmadı"

    event_id = await ctx.decisions.record_macro_event(
        series=event.series.value,
        kind=event.kind.value,
        observed_on=event.observed_on,
        previous_value=event.previous_value,
        new_value=event.new_value,
        change_bps=event.change_bps,
        surprise_bps=event.surprise_bps,
        triggered_rebalance=trigger is not None,
        note=note,
    )
    if event_id is None:
        log.info("poll_fed: %s on %s already processed", event.kind.value, event.observed_on)
        return None

    log.info("poll_fed: recorded %s (%s)", event.kind.value, event_id)
    if trigger is not None:
        await rebalance_all(ctx, trigger)
    return event_id


async def ingest_tefas(ctx: WorkerContext, window_days: int | None = None) -> int:
    """Pull the trailing NAV window for every fund we know about.

    A *window*, not "yesterday": TEFAS publishes late and revises, so re-reading
    the last week and upserting is the only way to end up with the same numbers
    the platform shows.  Idempotent by construction.
    """
    if ctx.tefas is None:
        log.debug("ingest_tefas: no TEFAS client configured, skipping")
        return 0

    end = date.today()
    start = end - timedelta(days=window_days or ctx.tefas_window_days)
    total = 0

    for fund_type in ctx.fund_types:
        frame = await ctx.tefas.history(start, end, fontip=fund_type)
        if frame.empty:
            log.warning("ingest_tefas: no %s rows for %s..%s", fund_type, start, end)
            continue
        rows = [
            {
                "fund_code": row.code,
                "nav_date": row.date.date() if hasattr(row.date, "date") else row.date,
                "nav": float(row.nav),
                "fund_type": fund_type,
                "shares": None if row.shares != row.shares else float(row.shares),
                "investors": None if row.investors != row.investors else int(row.investors),
            }
            for row in frame.itertuples()
        ]
        total += await ctx.decisions.upsert_navs(rows)

    log.info("ingest_tefas: upserted %d NAV rows for %s..%s", total, start, end)
    return total


async def rebalance_portfolio(
    ctx: WorkerContext, portfolio_id: str, trigger: RebalanceTrigger
) -> str | None:
    """Run the full pipeline for one portfolio and persist the decision.

    Every failure mode here ends in "hold the current book and log": an
    unparseable mandate, too little history, an infeasible LP.  Trading on a
    half-estimated model is worse than not trading.
    """
    loaded = await ctx.portfolios.load_mandate(portfolio_id)
    if loaded is None:
        log.warning("rebalance: portfolio %s not found", portfolio_id)
        return None
    mandate_json, class_of, notional, _currency = loaded

    try:
        mandate = parse_mandate(mandate_json, class_of)
    except MandateError as exc:
        log.error("rebalance: portfolio %s has an unusable mandate: %s", portfolio_id, exc)
        return None

    end = date.today()
    start = end - timedelta(days=ctx.lookback_days)
    returns, factors = await build_panel(ctx.session_factory, mandate.universe, start, end)

    if returns.empty or len(returns) < MIN_OBSERVATIONS:
        log.warning(
            "rebalance: portfolio %s has %d observations, need %d - holding",
            portfolio_id, len(returns), MIN_OBSERVATIONS,
        )
        return None

    symbols = [s for s in mandate.universe if s in returns.columns]
    models = fit_panel(returns[symbols], factors)
    symbols = [s for s in symbols if s in models]
    if not symbols:
        log.warning("rebalance: portfolio %s - no instrument survived estimation", portfolio_id)
        return None

    current = await ctx.decisions.current_weights(portfolio_id, symbols)

    engine = RebalanceEngine(mandate.to_constraints(), objective=mandate.objective)
    decision = engine.run(
        portfolio_id=portfolio_id,
        trigger=trigger,
        symbols=symbols,
        returns=returns[symbols],
        factors=factors,
        models=models,
        current_weights=current,
        notional=notional,
    )

    # Record even when nothing is traded: "we looked and decided not to move"
    # is exactly the answer a customer asking about a FED cut needs, and
    # without the row there is no evidence it happened.
    decision_id = await ctx.decisions.record(decision, trigger.scenario, current)
    log.info(
        "rebalance: portfolio %s -> decision %s (applied=%s, %d orders)",
        portfolio_id, decision_id, decision.applied, len(decision.orders),
    )
    return decision_id


async def rebalance_all(ctx: WorkerContext, trigger: RebalanceTrigger) -> list[str]:
    """Fan out over every portfolio.  One failure must not stop the rest."""
    decision_ids: list[str] = []
    for portfolio_id in await ctx.portfolios.all_ids():
        try:
            decision_id = await rebalance_portfolio(ctx, portfolio_id, trigger)
        except Exception:
            log.exception("rebalance: portfolio %s failed", portfolio_id)
            continue
        if decision_id is not None:
            decision_ids.append(decision_id)

    log.info("rebalance_all: %d decisions from %s", len(decision_ids), trigger.reason)
    return decision_ids


async def heartbeat(ctx: WorkerContext) -> datetime:
    """Cheap liveness signal for the scheduler's own monitoring."""
    now = datetime.now(timezone.utc)
    log.debug("worker heartbeat %s", now.isoformat())
    return now
