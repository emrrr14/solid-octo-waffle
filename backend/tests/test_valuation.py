import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.domain.models import AssetClass, Instrument, Portfolio, Position, Venue
from app.market.cache import Quote
from app.market.valuation import value_portfolio

NVDA = Instrument("NVDA", Venue.NASDAQ, "USD", AssetClass.EQUITY_GLOBAL, True)
THYAO = Instrument("THYAO.IS", Venue.BIST, "TRY", AssetClass.EQUITY_TR, True)


class FakeCache:
    def __init__(self, quotes, prev):
        self._quotes, self._prev = quotes, prev

    async def get_many(self, symbols):
        return {s: self._quotes[s] for s in symbols if s in self._quotes}

    async def prev_closes(self, symbols):
        return {s: self._prev[s] for s in symbols if s in self._prev}


class FakeFx:
    async def rate(self, frm, to):
        return 1.0 if frm == to else 34.0   # USDTRY


@pytest.mark.asyncio
async def test_valuation_converts_and_computes_day_pnl():
    now_ms = int(time.time() * 1000)
    cache = FakeCache(
        {"NVDA": Quote(110.0, now_ms), "THYAO.IS": Quote(300.0, now_ms)},
        {"NVDA": 100.0, "THYAO.IS": 290.0},
    )
    portfolio = Portfolio(
        "p1", "TRY",
        [Position(NVDA, Decimal("10"), Decimal("90")),
         Position(THYAO, Decimal("100"), Decimal("250"))],
        cash=Decimal("500"),
    )
    val = await value_portfolio(portfolio, cache, FakeFx(), now=datetime.now(timezone.utc))

    assert val.total_value == pytest.approx(10 * 110 * 34 + 100 * 300 + 500)
    assert val.day_pnl == pytest.approx(10 * 10 * 34 + 100 * 10)
    assert all(not p.stale for p in val.positions)


@pytest.mark.asyncio
async def test_missing_tick_falls_back_to_prev_close_and_flags_stale():
    cache = FakeCache({}, {"NVDA": 100.0})
    portfolio = Portfolio("p2", "USD", [Position(NVDA, Decimal("5"), Decimal("90"))])
    val = await value_portfolio(portfolio, cache, FakeFx())

    assert val.total_value == pytest.approx(500.0)
    assert val.day_pnl == pytest.approx(0.0)
    assert val.positions[0].stale


@pytest.mark.asyncio
async def test_old_tick_is_marked_stale():
    old_ms = int(time.time() * 1000) - 60_000
    cache = FakeCache({"NVDA": Quote(110.0, old_ms)}, {"NVDA": 100.0})
    portfolio = Portfolio("p3", "USD", [Position(NVDA, Decimal("1"), Decimal("90"))])
    val = await value_portfolio(portfolio, cache, FakeFx())
    assert val.positions[0].stale
