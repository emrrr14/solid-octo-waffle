"""Building the estimation panel from what is in the database.

The factor model needs two aligned frames: fund/instrument returns and macro
shocks.  Assembling them is where the subtle mistakes live, so the alignment
rules are explicit and unit-tested:

* **Log returns.**  They add across time, which the model assumes, and they keep
  a single 40% TRY move from dominating the fit the way a simple return would.
* **Shocks land on the next pricing day.**  The FOMC statement is at 18:00 UTC -
  after the BIST close and long after TEFAS has struck that day's NAV.  Charging
  the shock to the announcement date regresses fund returns against a decision
  that had not happened yet when the NAV was set, and manufactures beta out of
  nothing.  So an event is attributed to the first pricing date **at or after**
  it (a Friday-night FOMC hits Monday's NAV).
* **Sparse macro, dense returns.**  Events are a handful of rows a year; the
  regressors are zero on every other day, which is exactly right - zero means
  "no shock", and that is the state alpha already absorbs.
"""
from __future__ import annotations

import logging
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db import models
from app.macro.triggers import F_FED_CHANGE, F_FED_SURPRISE, F_TR_CPI_SURPRISE, F_USDTRY

log = logging.getLogger(__name__)

FX_SYMBOL = "USDTRY"


def to_log_returns(wide: pd.DataFrame) -> pd.DataFrame:
    """Wide price/NAV frame -> log returns.

    A single missing publication is forward-filled (funds skip holidays and the
    occasional day); a longer gap is left as NaN so the regression drops those
    rows rather than inventing a flat return.
    """
    filled = wide.sort_index().ffill(limit=1)
    return np.log(filled).diff().dropna(how="all")


def drop_degenerate_factors(factors: pd.DataFrame, min_events: int = 2) -> pd.DataFrame:
    """Remove regressors that carry no information in this window.

    A factor with no events in the lookback (or a single one) is a column of
    zeros with at most one spike.  Left in, it makes the design matrix
    rank-deficient - OLS then returns coefficients that are not uniquely
    determined, and the LP happily allocates against them.  Dropping is the
    honest response: we cannot estimate a sensitivity we have never observed.
    """
    keep = []
    for column in factors.columns:
        series = factors[column]
        if series.std(ddof=0) == 0 or int((series != 0).sum()) < min_events:
            log.info("panel: dropping factor %s (%d non-zero observations)",
                     column, int((series != 0).sum()))
            continue
        keep.append(column)
    return factors[keep]


def attribute_events(
    events: pd.DataFrame,
    index: pd.DatetimeIndex,
) -> pd.DataFrame:
    """Map sparse macro events onto the pricing calendar.

    ``events`` needs columns ``observed_on``, ``series``, ``kind``,
    ``change_bps`` and ``surprise_bps``.  Returns a frame indexed like ``index``
    with one column per regressor, zero where nothing happened.
    """
    columns = [F_FED_CHANGE, F_FED_SURPRISE, F_TR_CPI_SURPRISE]
    factors = pd.DataFrame(0.0, index=index, columns=columns)
    if events.empty or len(index) == 0:
        return factors

    ordered = index.sort_values()
    for _, event in events.iterrows():
        observed = pd.Timestamp(event["observed_on"]).normalize()
        # searchsorted("left") -> the first pricing date at or after the event.
        position = ordered.searchsorted(observed, side="left")
        if position >= len(ordered):
            continue  # announced after the panel ends; nothing priced it yet
        effective = ordered[position]

        if str(event["series"]).startswith(("DFEDTARU", "EFFR")):
            factors.loc[effective, F_FED_CHANGE] += float(event["change_bps"])
            factors.loc[effective, F_FED_SURPRISE] += float(event["surprise_bps"])
        elif str(event["kind"]) == "inflation_print":
            factors.loc[effective, F_TR_CPI_SURPRISE] += float(event["surprise_bps"]) / 100.0

    return factors


async def _fund_navs(
    session: AsyncSession, codes: list[str], start: date, end: date
) -> pd.DataFrame:
    rows = (
        await session.execute(
            select(models.FundNav.nav_date, models.FundNav.fund_code, models.FundNav.nav).where(
                models.FundNav.fund_code.in_(codes),
                models.FundNav.nav_date >= start,
                models.FundNav.nav_date <= end,
            )
        )
    ).all()
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows, columns=["date", "code", "nav"])
    frame["nav"] = frame["nav"].astype(float)
    return frame.pivot(index="date", columns="code", values="nav")


async def _daily_closes(
    session: AsyncSession, symbols: list[str], start: date, end: date
) -> pd.DataFrame:
    if not symbols:
        return pd.DataFrame()
    rows = (
        await session.execute(
            select(models.DailyClose.close_date, models.DailyClose.symbol, models.DailyClose.close).where(
                models.DailyClose.symbol.in_(symbols),
                models.DailyClose.close_date >= start,
                models.DailyClose.close_date <= end,
            )
        )
    ).all()
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame(rows, columns=["date", "symbol", "close"])
    frame["close"] = frame["close"].astype(float)
    return frame.pivot(index="date", columns="symbol", values="close")


async def _macro_events(session: AsyncSession, start: date, end: date) -> pd.DataFrame:
    rows = (
        await session.execute(
            select(
                models.MacroEventRow.observed_on,
                models.MacroEventRow.series,
                models.MacroEventRow.kind,
                models.MacroEventRow.change_bps,
                models.MacroEventRow.surprise_bps,
            ).where(
                models.MacroEventRow.observed_on >= start,
                models.MacroEventRow.observed_on <= end,
            )
        )
    ).all()
    return pd.DataFrame(
        rows, columns=["observed_on", "series", "kind", "change_bps", "surprise_bps"]
    )


async def build_panel(
    factory: async_sessionmaker[AsyncSession],
    fund_codes: list[str],
    start: date,
    end: date,
    fx_symbol: str = FX_SYMBOL,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return ``(returns, factors)`` ready for ``fit_panel``.

    Rows where every fund is missing are dropped; the FX factor is included only
    when there is a USDTRY series to build it from, because a column of zeros
    would be estimated as a beta of zero and quietly mislead.
    """
    async with factory() as session:
        navs = await _fund_navs(session, fund_codes, start, end)
        closes = await _daily_closes(session, [fx_symbol], start, end)
        events = await _macro_events(session, start, end)

    if navs.empty:
        log.warning("panel: no NAV history for %s between %s and %s", fund_codes, start, end)
        return pd.DataFrame(), pd.DataFrame()

    returns = to_log_returns(navs)
    returns.index = pd.DatetimeIndex(returns.index)

    factors = attribute_events(events, returns.index)

    if not closes.empty and fx_symbol in closes:
        fx = to_log_returns(closes[[fx_symbol]])
        fx.index = pd.DatetimeIndex(fx.index)
        factors[F_USDTRY] = fx[fx_symbol].reindex(factors.index).fillna(0.0)

    return returns, drop_degenerate_factors(factors)
