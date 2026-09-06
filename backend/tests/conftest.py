"""Shared fixtures: a real app on a throwaway SQLite database.

SQLite rather than Postgres so the suite runs anywhere in milliseconds; the
models stay portable precisely so this is possible.  A *file* database, not
``:memory:`` - each aiosqlite connection would otherwise get its own empty
in-memory database and the repositories would silently see nothing.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
import pytest_asyncio
from fastapi import FastAPI

from app.api import auth as auth_api
from app.api import routes as rest_api
from app.api import ws as ws_api
from app.db import models
from app.db.repositories import DecisionRepository, PortfolioRepository, UserRepository
from app.db.session import create_all, create_engine, create_session_factory
from app.market.cache import Quote
from app.security.tokens import TokenSettings

TEST_SECRET = "test-secret-key-that-is-long-enough!!"
EMAIL = "demo@example.com"
PASSWORD = "demo-password-123"
OTHER_EMAIL = "other@example.com"
OTHER_PASSWORD = "other-password-123"
PORTFOLIO_ID = "p-demo"


@pytest_asyncio.fixture
async def db(tmp_path):
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/test.db")
    await create_all(engine)
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def seeded(db):
    """One user, one portfolio with two positions, one decision, one macro event."""
    async with db() as session:
        users = UserRepository(session)
        user = await users.create(EMAIL, PASSWORD)
        # A second account, so isolation can be tested without hand-rolling one
        # inside a test that is already driving a synchronous client.
        other = await users.create(OTHER_EMAIL, OTHER_PASSWORD)
        session.add_all(
            [
                models.Instrument(symbol="NVDA", name="NVIDIA", venue="NASDAQ", currency="USD",
                                  asset_class="equity_global", streaming=True),
                models.Instrument(symbol="GLD", name="Altın Fonu", venue="TEFAS", currency="TRY",
                                  asset_class="gold", streaming=False),
                models.Portfolio(id=PORTFOLIO_ID, user_id=user.id, base_currency="TRY",
                                 cash=Decimal("100.00"), notional=Decimal("500.00")),
            ]
        )
        await session.flush()
        session.add_all(
            [
                models.Position(portfolio_id=PORTFOLIO_ID, symbol="NVDA",
                                quantity=Decimal("2"), avg_cost=Decimal("100")),
                models.Position(portfolio_id=PORTFOLIO_ID, symbol="GLD",
                                quantity=Decimal("100"), avg_cost=Decimal("1.0")),
            ]
        )
        decision = models.RebalanceDecisionRow(
            portfolio_id=PORTFOLIO_ID,
            decided_at=datetime.now(timezone.utc),
            trigger_reason="rate_cut -25bp",
            scenario={"fed_surprise_bps": -25.0},
            weights_before={"NVDA": 0.6, "GLD": 0.4},
            weights_after={"NVDA": 0.4, "GLD": 0.6},
            amounts={"NVDA": "200.00", "GLD": "300.00"},
            orders={"NVDA": "-100.00", "GLD": "100.00"},
            expected_return=0.001,
            risk_mad=0.002,
            turnover=0.4,
            binding_constraints=["turnover_budget"],
            solver_status="Optimal",
            applied=True,
        )
        session.add(decision)
        session.add(
            models.MacroEventRow(
                series="DFEDTARU", kind="rate_cut", observed_on=datetime(2026, 9, 5).date(),
                previous_value=5.5, new_value=5.25, change_bps=-25.0, surprise_bps=-25.0,
                triggered_rebalance=True,
            )
        )
        await session.commit()
        return {"user_id": user.id, "other_user_id": other.id, "decision_id": decision.id}


class FakeCache:
    """Stands in for Redis: the valuation path only needs these two reads."""

    def __init__(self, quotes: dict, prev: dict) -> None:
        self._quotes, self._prev = quotes, prev

    async def get_many(self, symbols):
        return {s: self._quotes[s] for s in symbols if s in self._quotes}

    async def prev_closes(self, symbols):
        return {s: self._prev[s] for s in symbols if s in self._prev}


class FakeFx:
    async def rate(self, frm, to):
        return 1.0 if frm == to else 34.0


@pytest.fixture
def app(db, seeded):
    application = FastAPI()
    application.include_router(auth_api.router)
    application.include_router(rest_api.router)
    application.include_router(ws_api.router)

    application.state.token_settings = TokenSettings(secret=TEST_SECRET)
    application.state.session_factory = db
    application.state.portfolios = PortfolioRepository(db)
    application.state.decisions = DecisionRepository(db)
    application.state.seeded = seeded

    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    application.state.cache = FakeCache(
        {"NVDA": Quote(110.0, now_ms), "GLD": Quote(1.10, now_ms)},
        {"NVDA": 100.0, "GLD": 1.00},
    )
    application.state.fx = FakeFx()
    return application


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def tokens(client):
    response = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def auth_headers(tokens):
    return {"Authorization": f"Bearer {tokens['access_token']}"}
