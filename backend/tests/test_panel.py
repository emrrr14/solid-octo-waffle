"""Panel assembly - where the subtle alignment mistakes live."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest
import pytest_asyncio

from app.analytics.panel import (
    attribute_events,
    build_panel,
    drop_degenerate_factors,
    to_log_returns,
)
from app.db import models
from app.macro.triggers import F_FED_CHANGE, F_FED_SURPRISE, F_TR_CPI_SURPRISE, F_USDTRY

INDEX = pd.DatetimeIndex(pd.bdate_range("2026-09-01", periods=10))


def event(observed_on: str, series="DFEDTARU", kind="rate_cut", change=-25.0, surprise=-25.0):
    return {
        "observed_on": observed_on,
        "series": series,
        "kind": kind,
        "change_bps": change,
        "surprise_bps": surprise,
    }


class TestLogReturns:
    def test_log_returns_add_across_time(self):
        wide = pd.DataFrame({"A": [1.0, 1.10, 1.21]}, index=pd.bdate_range("2026-09-01", periods=3))
        returns = to_log_returns(wide)
        assert returns["A"].sum() == pytest.approx(np.log(1.21))

    def test_single_missing_day_is_bridged_not_faked(self):
        wide = pd.DataFrame(
            {"A": [1.0, np.nan, 1.02]}, index=pd.bdate_range("2026-09-01", periods=3)
        )
        returns = to_log_returns(wide)
        assert returns["A"].iloc[0] == pytest.approx(0.0)          # carried forward
        assert returns["A"].iloc[1] == pytest.approx(np.log(1.02))  # not -100% then +inf

    def test_a_long_gap_is_left_missing_not_fabricated(self):
        # Fund A stops publishing for two days. Its return across the gap is
        # genuinely unobservable at daily frequency, so it must stay NaN and be
        # dropped per-fund by the regression - not smeared into a fake daily move.
        wide = pd.DataFrame(
            {"A": [1.0, np.nan, np.nan, 1.05], "B": [1.0, 1.01, 1.02, 1.03]},
            index=pd.bdate_range("2026-09-01", periods=4),
        )
        returns = to_log_returns(wide)
        assert len(returns) == 3               # B keeps every row
        assert returns["A"].isna().sum() == 2
        assert returns["B"].notna().all()


class TestEventAttribution:
    def test_shock_lands_on_the_announcement_day_when_it_is_a_pricing_day(self):
        factors = attribute_events(pd.DataFrame([event("2026-09-03")]), INDEX)
        assert factors.loc["2026-09-03", F_FED_SURPRISE] == -25.0
        assert factors[F_FED_SURPRISE].sum() == -25.0

    def test_a_weekend_announcement_moves_to_the_next_pricing_day(self):
        # The FOMC statement is at 18:00 UTC - after the BIST close and long
        # after TEFAS struck the day's NAV. Charging it to the announcement date
        # regresses returns against a decision that had not happened yet.
        factors = attribute_events(pd.DataFrame([event("2026-09-05")]), INDEX)  # Saturday
        assert factors.loc["2026-09-07", F_FED_SURPRISE] == -25.0

    def test_events_after_the_panel_are_dropped(self):
        factors = attribute_events(pd.DataFrame([event("2027-01-01")]), INDEX)
        assert factors[F_FED_SURPRISE].abs().sum() == 0.0

    def test_two_events_on_one_pricing_day_accumulate(self):
        factors = attribute_events(
            pd.DataFrame([event("2026-09-05"), event("2026-09-06")]), INDEX
        )
        assert factors.loc["2026-09-07", F_FED_SURPRISE] == -50.0

    def test_cpi_surprise_is_converted_to_percentage_points(self):
        factors = attribute_events(
            pd.DataFrame([event("2026-09-03", series="TR_CPI_YOY", kind="inflation_print",
                                change=-270.0, surprise=-80.0)]),
            INDEX,
        )
        assert factors.loc["2026-09-03", F_TR_CPI_SURPRISE] == pytest.approx(-0.8)
        assert factors.loc["2026-09-03", F_FED_CHANGE] == 0.0

    def test_no_events_gives_a_zero_frame_of_the_right_shape(self):
        factors = attribute_events(pd.DataFrame(), INDEX)
        assert list(factors.index) == list(INDEX)
        assert (factors == 0.0).all().all()


class TestDegenerateFactors:
    def test_unobserved_factors_are_dropped(self):
        factors = pd.DataFrame(
            {"seen": [0.0, -25.0, 0.0, 12.5], "never": [0.0] * 4, "once": [0.0, 0.0, 5.0, 0.0]},
            index=pd.bdate_range("2026-09-01", periods=4),
        )
        kept = drop_degenerate_factors(factors)
        # "never" is a zero column and "once" has a single spike: neither can be
        # estimated, and both make the design matrix rank-deficient.
        assert list(kept.columns) == ["seen"]


class TestBuildPanel:
    @pytest_asyncio.fixture
    async def history(self, db):
        rng = np.random.default_rng(3)
        days = [date.today() - timedelta(days=200 - i) for i in range(200)]
        days = [d for d in days if d.weekday() < 5]
        async with db() as session:
            for code, vol in (("AAA", 0.01), ("BBB", 0.004)):
                nav = 1.0
                for day in days:
                    nav *= float(np.exp(rng.normal(0.0004, vol)))
                    session.add(models.FundNav(fund_code=code, nav_date=day,
                                               nav=Decimal(f"{nav:.6f}"), fund_type="YAT"))
            rate = 30.0
            for day in days:
                rate *= float(np.exp(rng.normal(0.0003, 0.006)))
                session.add(models.DailyClose(symbol="USDTRY", close_date=day,
                                              close=Decimal(f"{rate:.6f}")))
            for offset in (0.8, 0.6, 0.4, 0.15):
                session.add(models.MacroEventRow(
                    series="DFEDTARU", kind="rate_cut", observed_on=days[int(len(days) * (1 - offset))],
                    previous_value=5.5, new_value=5.25, change_bps=-25.0,
                    surprise_bps=-25.0 if offset > 0.5 else 12.5,
                ))
            await session.commit()
        return days

    async def test_panel_has_aligned_returns_and_factors(self, db, history):
        returns, factors = await build_panel(
            db, ["AAA", "BBB"], date.today() - timedelta(days=300), date.today()
        )
        assert list(returns.columns) == ["AAA", "BBB"]
        assert returns.index.equals(factors.index)
        assert F_FED_SURPRISE in factors.columns
        assert F_USDTRY in factors.columns
        assert factors[F_FED_SURPRISE].abs().sum() > 0

    async def test_unknown_funds_give_an_empty_panel_not_a_crash(self, db, history):
        returns, factors = await build_panel(
            db, ["NOPE"], date.today() - timedelta(days=300), date.today()
        )
        assert returns.empty and factors.empty
