"""Seed a demo user, portfolio and rebalance decision.

    DEV_MODE=1 python -m scripts.seed_demo
    DEV_MODE=1 uvicorn app.main:app --reload

Gives you something to sign into from the iOS app:
    demo@roboadvisor.example / demo-password-123
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import numpy as np

from app.db import models
from app.db.migrate import upgrade_to_head
from app.db.repositories import UserRepository
from app.db.session import create_engine, create_session_factory
from app.settings import load_settings

EMAIL = "demo@roboadvisor.example"
PASSWORD = "demo-password-123"
PORTFOLIO_ID = "demo-500try"

INSTRUMENTS = [
    ("NVDA", "NVIDIA Corp", "NASDAQ", "USD", "equity_global", True),
    ("SPY", "S&P 500 ETF", "NYSE", "USD", "equity_global", True),
    ("THYAO.IS", "Türk Hava Yolları", "BIST", "TRY", "equity_tr", True),
    ("AFA", "BIST Hisse Fonu", "TEFAS", "TRY", "equity_tr", False),
    ("IPB", "Yabancı Hisse Fonu", "TEFAS", "TRY", "equity_global", False),
    ("TTE", "Devlet Tahvili Fonu", "TEFAS", "TRY", "bond_tr", False),
    ("GLD", "Altın Fonu", "TEFAS", "TRY", "gold", False),
    ("MMK", "Para Piyasası Fonu", "TEFAS", "TRY", "money_market", False),
    ("EUB", "Eurobond Fonu", "TEFAS", "TRY", "fx", False),
]

POSITIONS = [
    ("NVDA", Decimal("12"), Decimal("104.20")),
    ("SPY", Decimal("4"), Decimal("552.10")),
    ("THYAO.IS", Decimal("140"), Decimal("281.00")),
    ("AFA", Decimal("97.35"), Decimal("1.284")),
    ("GLD", Decimal("54.82"), Decimal("0.912")),
]

WEIGHTS_BEFORE = {"AFA": 0.25, "IPB": 0.20, "TTE": 0.20, "GLD": 0.10, "MMK": 0.10, "EUB": 0.15}
WEIGHTS_AFTER = {"AFA": 0.0613, "IPB": 0.0887, "TTE": 0.25, "GLD": 0.10, "MMK": 0.35, "EUB": 0.15}
AMOUNTS = {"AFA": "30.65", "IPB": "44.35", "TTE": "125.00", "GLD": "50.00", "MMK": "175.00", "EUB": "75.00"}
ORDERS = {"AFA": "-94.35", "IPB": "-55.65", "TTE": "25.00", "MMK": "125.00"}


# What the customer agreed the optimiser may do with the 500 TL sleeve.
MANDATE = {
    "universe": ["AFA", "IPB", "TTE", "GLD", "MMK", "EUB"],
    "class_upper": {"equity_tr": 0.35, "equity_global": 0.35, "gold": 0.25, "fx": 0.30},
    "class_lower": {"money_market": 0.05, "bond_tr": 0.10},
    "instrument_upper": {code: 0.35 for code in ["AFA", "IPB", "TTE", "GLD", "MMK", "EUB"]},
    "turnover_cap": 0.60,
    "min_return": 0.00045,
    "objective": "min_risk",
}

# Synthetic history so the worker has something to estimate against. The funds
# are generated *with* macro sensitivities and the matching FOMC events are
# seeded alongside, so the demo exercises the real path: betas are estimated
# from the panel rather than assumed. Replace with a real backfill
# (`TefasClient.history` over two years) before trusting any number here.
FUND_PARAMS = {
    # code: (daily drift, vol, beta to a 1bp FED surprise, beta to d_log_usdtry)
    "AFA": (0.00060, 0.016, -0.00022, -0.35),
    "IPB": (0.00070, 0.014, -0.00040, +0.80),
    "TTE": (0.00035, 0.004, -0.00008, -0.10),
    "GLD": (0.00050, 0.011, -0.00030, +0.90),
    "MMK": (0.00018, 0.0004, +0.00001, +0.00),
    "EUB": (0.00040, 0.006, -0.00015, +0.95),
}


def business_days(days: int) -> list[date]:
    start = date.today() - timedelta(days=days)
    return [d for i in range(days) if (d := start + timedelta(days=i)).weekday() < 5]


def synthetic_history(days: int = 600, seed: int = 11):
    """Return (fund NAV rows, USDTRY closes, macro event rows), mutually consistent."""
    rng = np.random.default_rng(seed)
    calendar = business_days(days)
    n = len(calendar)

    # An FOMC-like schedule: eight meetings a year, most of them fully priced.
    surprises = np.zeros(n)
    meeting_idx = sorted(rng.choice(range(20, n), size=max(2, n // 32), replace=False))
    for i in meeting_idx:
        surprises[i] = float(rng.choice([-25.0, -12.5, 0.0, 0.0, 12.5, 25.0]))

    fx_shock = 0.0004 * surprises + rng.normal(0, 0.005, n)

    fx_rows, rate = [], 30.0
    for day, shock in zip(calendar, fx_shock):
        rate *= float(np.exp(shock))
        fx_rows.append(
            models.DailyClose(symbol="USDTRY", close_date=day, close=Decimal(f"{rate:.6f}"), currency="TRY")
        )

    nav_rows = []
    for code, (drift, vol, beta_fed, beta_fx) in FUND_PARAMS.items():
        nav = 1.0
        shocks = drift + beta_fed * surprises + beta_fx * fx_shock + rng.normal(0, vol, n)
        for day, shock in zip(calendar, shocks):
            nav *= float(np.exp(shock))
            nav_rows.append(
                models.FundNav(fund_code=code, nav_date=day, nav=Decimal(f"{nav:.6f}"), fund_type="YAT")
            )

    level = 5.50
    event_rows = []
    for i in meeting_idx:
        if surprises[i] == 0.0:
            continue  # a fully-priced meeting leaves no trace in the model
        change = float(np.sign(surprises[i]) * 25.0)
        previous, level = level, round(level + change / 100.0, 2)
        event_rows.append(
            models.MacroEventRow(
                series="DFEDTARU",
                kind="rate_cut" if change < 0 else "rate_hike",
                observed_on=calendar[i],
                previous_value=previous,
                new_value=level,
                change_bps=change,
                surprise_bps=float(surprises[i]),
                triggered_rebalance=abs(surprises[i]) >= 10.0,
            )
        )
    return nav_rows, fx_rows, event_rows


async def main() -> None:
    settings = load_settings()
    engine = create_engine(settings.database_url)
    factory = create_session_factory(engine)

    async with factory() as session:
        user = await UserRepository(session).create(EMAIL, PASSWORD)

        for symbol, name, venue, ccy, cls, streaming in INSTRUMENTS:
            session.add(
                models.Instrument(
                    symbol=symbol, name=name, venue=venue, currency=ccy,
                    asset_class=cls, streaming=streaming,
                )
            )

        session.add(
            models.Portfolio(
                id=PORTFOLIO_ID, user_id=user.id, base_currency="TRY",
                cash=Decimal("250.00"), notional=Decimal("500.00"),
                mandate=MANDATE,
            )
        )
        for symbol, qty, cost in POSITIONS:
            session.add(
                models.Position(portfolio_id=PORTFOLIO_ID, symbol=symbol, quantity=qty, avg_cost=cost)
            )

        session.add(
            models.RebalanceDecisionRow(
                portfolio_id=PORTFOLIO_ID,
                decided_at=datetime.now(timezone.utc) - timedelta(hours=2),
                trigger_reason="rate_cut -25bp on 2026-09-05 (-25bp vs expectation)",
                scenario={"d_fed_bps": -25.0, "fed_surprise_bps": -25.0, "d_log_usdtry": -0.01},
                weights_before=WEIGHTS_BEFORE,
                weights_after=WEIGHTS_AFTER,
                amounts=AMOUNTS,
                orders=ORDERS,
                expected_return=0.00131,
                risk_mad=0.00194,
                turnover=0.60,
                binding_constraints=["turnover_budget"],
                solver_status="Optimization terminated successfully. (HiGHS Status 7: Optimal)",
                applied=True,
            )
        )

        navs, fx, events = synthetic_history()
        session.add_all(navs)
        session.add_all(fx)
        session.add_all(events)

        await session.commit()

    print(f"seeded {len(navs)} NAV rows, {len(fx)} FX closes, {len(events)} macro events")

    await engine.dispose()
    print(f"seeded: {EMAIL} / {PASSWORD}  portfolio={PORTFOLIO_ID}")


if __name__ == "__main__":
    # Migrations run their own event loop (see migrations/env.py), so they go
    # before asyncio.run rather than inside it.
    upgrade_to_head(load_settings().database_url)
    asyncio.run(main())
