"""Real-time portfolio valuation.

Valuation runs **server-side**.  The browser receives values, never the inputs
to compute them: prices are licensed data (redistribution rules differ per
venue), quantities are private, and a client that computes its own totals will
drift from the ledger the moment a corporate action or a partial fill lands.

Cost is O(positions) per tick batch, and the 1 Hz cadence means it runs once a
second per *connected* portfolio, not once per tick - a NVDA-heavy book can see
thousands of prints a second and none of them individually need a recompute.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.domain.models import (
    Portfolio,
    PortfolioValuation,
    PositionValuation,
    Venue,
)
from app.market.cache import LastPriceCache
from app.market.session import any_open


class FxConverter:
    """USDTRY and friends, from the same last-value cache as equities."""

    def __init__(self, cache: LastPriceCache) -> None:
        self._cache = cache

    async def rate(self, frm: str, to: str) -> float:
        if frm == to:
            return 1.0
        quotes = await self._cache.get_many([f"{frm}{to}", f"{to}{frm}"])
        if (direct := quotes.get(f"{frm}{to}")) is not None:
            return direct.price
        if (inverse := quotes.get(f"{to}{frm}")) is not None and inverse.price:
            return 1.0 / inverse.price
        raise KeyError(f"no FX rate for {frm}{to}")


async def value_portfolio(
    portfolio: Portfolio,
    cache: LastPriceCache,
    fx: FxConverter,
    staleness_budget_ms: int = 5_000,
    now: datetime | None = None,
) -> PortfolioValuation:
    now = now or datetime.now(timezone.utc)
    now_ms = int(now.timestamp() * 1000)
    symbols = portfolio.symbols()

    quotes = await cache.get_many(symbols)
    prev_closes = await cache.prev_closes(symbols)

    rates: dict[str, float] = {}
    for position in portfolio.positions:
        ccy = position.instrument.currency
        if ccy not in rates:
            rates[ccy] = await fx.rate(ccy, portfolio.base_currency)

    rows: list[PositionValuation] = []
    total = float(portfolio.cash)
    prev_total = float(portfolio.cash)

    for position in portfolio.positions:
        symbol = position.instrument.symbol
        quote = quotes.get(symbol)
        prev_close = prev_closes.get(symbol)
        # No tick yet (pre-open, illiquid name, feed gap) -> fall back to the
        # official close and flag it, rather than valuing the position at zero.
        price = quote.price if quote else (prev_close or 0.0)
        stale = quote is None or quote.is_stale(staleness_budget_ms, now_ms)
        rate = rates[position.instrument.currency]
        qty = float(position.quantity)

        market_value = qty * price * rate
        prev_value = qty * (prev_close if prev_close is not None else price) * rate
        total += market_value
        prev_total += prev_value

        rows.append(
            PositionValuation(
                symbol=symbol,
                quantity=qty,
                last_price=price,
                price_currency=position.instrument.currency,
                fx_rate=rate,
                market_value_base=market_value,
                prev_close_base=prev_value,
                stale=stale,
            )
        )

    day_pnl = total - prev_total
    venues: list[Venue] = [p.instrument.venue for p in portfolio.positions]

    return PortfolioValuation(
        portfolio_id=portfolio.portfolio_id,
        base_currency=portfolio.base_currency,
        ts=now,
        total_value=total,
        day_pnl=day_pnl,
        day_pnl_pct=(day_pnl / prev_total * 100.0) if prev_total else 0.0,
        positions=rows,
        session=any_open(venues, now),
    )
