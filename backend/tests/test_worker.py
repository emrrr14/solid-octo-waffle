"""Worker jobs and the locking that keeps two replicas from doubling up."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio

from app.db import models
from app.db.repositories import DecisionRepository, PortfolioRepository
from app.domain.models import MacroEvent, MacroEventKind, MacroSeries
from app.macro.triggers import TriggerRouter
from app.worker.jobs import WorkerContext, ingest_tefas, poll_fed, rebalance_all, rebalance_portfolio
from app.worker.lock import InMemoryLockBackend, job_lock
from tests.conftest import PORTFOLIO_ID

UNIVERSE = ["AAA", "BBB", "CCC", "DDD"]
MANDATE = {
    "universe": UNIVERSE,
    "class_upper": {"equity": 0.5, "gold": 0.3},
    "class_lower": {"money": 0.05},
    "instrument_upper": {code: 0.5 for code in UNIVERSE},
    "turnover_cap": 0.8,
    "objective": "min_risk",
}
CLASSES = {"AAA": "equity", "BBB": "bond", "CCC": "gold", "DDD": "money"}
BETAS = {"AAA": -0.00022, "BBB": -0.00008, "CCC": -0.00030, "DDD": 0.00001}
VOLS = {"AAA": 0.016, "BBB": 0.004, "CCC": 0.011, "DDD": 0.0004}


class FakeFed:
    """Stands in for FedRateMonitor: returns a scripted sequence of polls."""

    def __init__(self, events: list[MacroEvent | None]) -> None:
        self._events = list(events)
        self.calls = 0

    async def poll(self, expected_rate_pct=None):
        self.calls += 1
        return self._events.pop(0) if self._events else None


class FakeTefas:
    def __init__(self, frame: pd.DataFrame) -> None:
        self._frame = frame
        self.windows: list[tuple[date, date, str]] = []

    async def history(self, start, end, fund_codes=None, fontip="YAT"):
        self.windows.append((start, end, fontip))
        return self._frame


async def count_decisions(ctx: WorkerContext) -> int:
    async with ctx.session_factory() as session:
        rows = (await session.execute(models.RebalanceDecisionRow.__table__.select())).all()
    return len(rows)


def cut_event(observed_on: date, surprise: float = -25.0) -> MacroEvent:
    return MacroEvent(
        series=MacroSeries.FED_TARGET_UPPER,
        kind=MacroEventKind.RATE_CUT,
        observed_on=observed_on,
        previous_value=5.50,
        new_value=5.25,
        change_bps=-25.0,
        surprise_bps=surprise,
    )


@pytest_asyncio.fixture
async def history(db, seeded):
    """Two years of fund NAVs generated with real macro sensitivities, plus the
    matching FOMC events - so the factor model has something true to find."""
    rng = np.random.default_rng(5)
    days = [d for i in range(400) if (d := date.today() - timedelta(days=400 - i)).weekday() < 5]
    n = len(days)

    surprises = np.zeros(n)
    meetings = sorted(rng.choice(range(10, n), size=12, replace=False))
    for i in meetings:
        surprises[i] = float(rng.choice([-25.0, -12.5, 12.5, 25.0]))

    async with db() as session:
        for code in UNIVERSE:
            nav = 1.0
            shocks = 0.0004 + BETAS[code] * surprises + rng.normal(0, VOLS[code], n)
            for day, shock in zip(days, shocks):
                nav *= float(np.exp(shock))
                session.add(models.FundNav(fund_code=code, nav_date=day,
                                           nav=Decimal(f"{nav:.6f}"), fund_type="YAT"))
            session.add(models.Instrument(symbol=code, name=f"{code} Fonu", venue="TEFAS",
                                          currency="TRY", asset_class=CLASSES[code], streaming=False))

        level = 5.5
        for i in meetings:
            change = float(np.sign(surprises[i]) * 25.0)
            previous, level = level, round(level + change / 100.0, 2)
            session.add(models.MacroEventRow(
                series="DFEDTARU", kind="rate_cut" if change < 0 else "rate_hike",
                observed_on=days[i], previous_value=previous, new_value=level,
                change_bps=change, surprise_bps=float(surprises[i]), triggered_rebalance=True,
            ))

        portfolio = (await session.execute(
            models.Portfolio.__table__.select().where(models.Portfolio.id == PORTFOLIO_ID)
        )).first()
        assert portfolio is not None
        await session.execute(
            models.Portfolio.__table__.update()
            .where(models.Portfolio.id == PORTFOLIO_ID)
            .values(mandate=MANDATE)
        )
        await session.commit()
    return days


@pytest.fixture
def ctx(db):
    return WorkerContext(
        portfolios=PortfolioRepository(db),
        decisions=DecisionRepository(db),
        session_factory=db,
        router=TriggerRouter(),
    )


class TestPollFed:
    async def test_no_step_writes_nothing(self, ctx):
        ctx.fed = FakeFed([None])
        assert await poll_fed(ctx) is None
        assert await ctx.decisions.macro_events() == []

    async def test_a_surprise_cut_records_the_event(self, ctx, seeded):
        ctx.fed = FakeFed([cut_event(date.today())])
        event_id = await poll_fed(ctx)

        assert event_id is not None
        recorded = await ctx.decisions.macro_events()
        assert recorded[0].triggered_rebalance is True
        assert recorded[0].surprise_bps == -25.0

    async def test_a_priced_in_cut_is_recorded_but_does_not_trade(self, ctx, seeded, history):
        ctx.fed = FakeFed([cut_event(date.today(), surprise=0.0)])
        before = len(await ctx.decisions.macro_events())
        decisions_before = await count_decisions(ctx)

        await poll_fed(ctx)

        events = await ctx.decisions.macro_events(limit=100)
        assert len(events) == before + 1
        latest = max(events, key=lambda e: e.created_at)
        assert latest.triggered_rebalance is False
        assert latest.note  # tells the user why nothing happened
        assert await count_decisions(ctx) == decisions_before  # nothing traded

    async def test_republished_decision_cannot_replay_a_rebalance(self, ctx, seeded, history):
        # FRED revises and re-publishes; the same decision must be processed once.
        event = cut_event(date.today())
        ctx.fed = FakeFed([event, event])

        before = await count_decisions(ctx)
        first = await poll_fed(ctx)
        after_first = await count_decisions(ctx)
        second = await poll_fed(ctx)

        assert first is not None
        assert second is None
        assert after_first == before + 1              # the first poll traded
        assert await count_decisions(ctx) == after_first  # the replay did not

    async def test_missing_fred_client_is_a_no_op(self, ctx):
        assert await poll_fed(ctx) is None


class TestIngestTefas:
    async def test_navs_are_upserted_idempotently(self, ctx, db):
        frame = pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-09-01", "2026-09-02"]),
                "code": ["AAA", "AAA"],
                "title": ["A", "A"],
                "nav": [1.10, 1.11],
                "shares": [100.0, 101.0],
                "investors": [10, 11],
            }
        )
        ctx.tefas = FakeTefas(frame)
        ctx.fund_types = ("YAT",)

        assert await ingest_tefas(ctx) == 2
        assert await ingest_tefas(ctx) == 2  # re-running the window is a no-op

        async with db() as session:
            rows = (await session.execute(models.FundNav.__table__.select())).all()
        assert len(rows) == 2

    async def test_both_fund_types_are_pulled(self, ctx):
        ctx.tefas = FakeTefas(pd.DataFrame())
        await ingest_tefas(ctx, window_days=3)
        assert [w[2] for w in ctx.tefas.windows] == ["YAT", "EMK"]
        assert (ctx.tefas.windows[0][1] - ctx.tefas.windows[0][0]).days == 3

    async def test_missing_client_is_a_no_op(self, ctx):
        assert await ingest_tefas(ctx) == 0


class TestRebalance:
    async def test_decision_is_persisted_and_readable_by_the_api(self, ctx, seeded, history):
        trigger = TriggerRouter().evaluate(cut_event(date.today()))
        decision_id = await rebalance_portfolio(ctx, PORTFOLIO_ID, trigger)

        assert decision_id is not None
        record = await ctx.decisions.latest(PORTFOLIO_ID)
        assert record.decision_id == decision_id
        assert record.trigger_reason.startswith("rate_cut")
        assert sum(row.target_weight for row in record.rows) == pytest.approx(1.0, abs=1e-6)
        assert sum(row.target_amount for row in record.rows) == pytest.approx(500.0, abs=0.01)

    async def test_mandate_bands_are_respected_by_the_solution(self, ctx, seeded, history):
        trigger = TriggerRouter().evaluate(cut_event(date.today()))
        await rebalance_portfolio(ctx, PORTFOLIO_ID, trigger)

        weights = {row.symbol: row.target_weight for row in (await ctx.decisions.latest(PORTFOLIO_ID)).rows}
        assert weights["AAA"] <= 0.5 + 1e-6          # equity cap
        assert weights["CCC"] <= 0.3 + 1e-6          # gold cap
        assert weights["DDD"] >= 0.05 - 1e-6         # money-market floor

    async def test_too_little_history_holds_the_book(self, ctx, seeded):
        # No NAV history seeded: the model cannot be estimated, so nothing trades.
        trigger = TriggerRouter().evaluate(cut_event(date.today()))
        assert await rebalance_portfolio(ctx, PORTFOLIO_ID, trigger) is None

    async def test_unknown_portfolio_is_skipped(self, ctx, seeded):
        trigger = TriggerRouter().evaluate(cut_event(date.today()))
        assert await rebalance_portfolio(ctx, "ghost", trigger) is None

    async def test_one_failing_portfolio_does_not_stop_the_others(self, ctx, seeded, history, db):
        async with db() as session:
            session.add(models.Portfolio(id="broken", user_id=seeded["user_id"],
                                         base_currency="TRY", notional=Decimal("100"),
                                         mandate={"universe": []}))  # invalid mandate
            await session.commit()

        trigger = TriggerRouter().evaluate(cut_event(date.today()))
        decision_ids = await rebalance_all(ctx, trigger)

        assert len(decision_ids) == 1  # the good portfolio still got its decision


class TestJobLock:
    async def test_a_second_runner_is_turned_away(self):
        backend = InMemoryLockBackend()
        async with job_lock(backend, "poll_fed") as first:
            assert first is True
            async with job_lock(backend, "poll_fed") as second:
                assert second is False

    async def test_the_lock_is_released_afterwards(self):
        backend = InMemoryLockBackend()
        async with job_lock(backend, "poll_fed"):
            pass
        async with job_lock(backend, "poll_fed") as acquired:
            assert acquired is True

    async def test_the_lock_is_released_even_when_the_job_raises(self):
        backend = InMemoryLockBackend()
        with pytest.raises(RuntimeError):
            async with job_lock(backend, "poll_fed"):
                raise RuntimeError("job blew up")
        async with job_lock(backend, "poll_fed") as acquired:
            assert acquired is True

    async def test_different_jobs_do_not_block_each_other(self):
        backend = InMemoryLockBackend()
        async with job_lock(backend, "poll_fed") as a, job_lock(backend, "ingest_tefas") as b:
            assert a and b
