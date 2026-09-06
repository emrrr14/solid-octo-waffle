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

from app.db import models
from app.db.repositories import UserRepository
from app.db.session import create_all, create_engine, create_session_factory
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


async def main() -> None:
    settings = load_settings()
    engine = create_engine(settings.database_url)
    await create_all(engine)
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

        for series, kind, day, prev, new, change, surprise, fired, note in [
            ("DFEDTARU", "rate_cut", date(2026, 9, 5), 5.50, 5.25, -25.0, -25.0, True, ""),
            ("DFEDTARU", "rate_cut", date(2026, 7, 30), 5.75, 5.50, -25.0, 0.0, False,
             "Piyasada fiyatlanmıştı, işlem yapılmadı"),
            ("TR_CPI_YOY", "inflation_print", date(2026, 7, 3), 38.1, 35.4, -270.0, -80.0, True, ""),
        ]:
            session.add(
                models.MacroEventRow(
                    series=series, kind=kind, observed_on=day, previous_value=prev,
                    new_value=new, change_bps=change, surprise_bps=surprise,
                    triggered_rebalance=fired, note=note,
                )
            )

        await session.commit()

    await engine.dispose()
    print(f"seeded: {EMAIL} / {PASSWORD}  portfolio={PORTFOLIO_ID}")


if __name__ == "__main__":
    asyncio.run(main())
