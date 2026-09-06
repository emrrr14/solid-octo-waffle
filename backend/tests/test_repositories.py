"""Repository behaviour that the API tests cannot reach directly."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.db import models
from app.db.repositories import (
    AuthError,
    DecisionRepository,
    PortfolioRepository,
    RefreshTokenRepository,
    UserRepository,
)
from app.security.tokens import TokenSettings
from tests.conftest import EMAIL, PASSWORD, PORTFOLIO_ID

SETTINGS = TokenSettings(secret="test-secret-key-that-is-long-enough!!")


async def test_duplicate_email_is_rejected(db, seeded):
    async with db() as session:
        with pytest.raises(AuthError):
            await UserRepository(session).create(EMAIL, PASSWORD)


async def test_authenticate_rejects_unknown_user_without_leaking(db, seeded):
    async with db() as session:
        with pytest.raises(AuthError):
            await UserRepository(session).authenticate("ghost@example.com", PASSWORD)


async def test_inactive_user_cannot_sign_in(db, seeded):
    async with db() as session:
        user = await UserRepository(session).authenticate(EMAIL, PASSWORD)
        user.is_active = False
        await session.commit()
    async with db() as session:
        with pytest.raises(AuthError):
            await UserRepository(session).authenticate(EMAIL, PASSWORD)


async def test_expired_refresh_token_is_rejected(db, seeded):
    async with db() as session:
        repo = RefreshTokenRepository(session, SETTINGS)
        token = await repo.issue(seeded["user_id"])
        await session.commit()

        row = (await session.execute(models.RefreshToken.__table__.select())).first()
        await session.execute(
            models.RefreshToken.__table__.update()
            .where(models.RefreshToken.token_hash == row.token_hash)
            .values(expires_at=datetime.now(timezone.utc) - timedelta(days=1))
        )
        await session.commit()

        with pytest.raises(AuthError, match="expired"):
            await repo.rotate(token)


async def test_revoked_family_cannot_refresh(db, seeded):
    async with db() as session:
        repo = RefreshTokenRepository(session, SETTINGS)
        token = await repo.issue(seeded["user_id"], family_id="fam-1")
        await repo.revoke_family("fam-1")
        await session.commit()
        with pytest.raises(AuthError, match="revoked"):
            await repo.rotate(token)


async def test_rotation_keeps_the_family(db, seeded):
    async with db() as session:
        repo = RefreshTokenRepository(session, SETTINGS)
        first = await repo.issue(seeded["user_id"], family_id="fam-2")
        await session.commit()
        second, user_id = await repo.rotate(first)
        await session.commit()

        assert user_id == seeded["user_id"]
        rows = (await session.execute(models.RefreshToken.__table__.select())).all()
        assert {r.family_id for r in rows} == {"fam-2"}
        assert second != first


async def test_portfolio_loads_as_a_domain_object(db, seeded):
    portfolio = await PortfolioRepository(db).get(PORTFOLIO_ID)
    assert portfolio is not None
    assert portfolio.base_currency == "TRY"
    assert portfolio.cash == Decimal("100.00")
    assert set(portfolio.symbols()) == {"NVDA", "GLD"}

    nvda = next(p for p in portfolio.positions if p.instrument.symbol == "NVDA")
    assert nvda.instrument.currency == "USD"
    assert nvda.instrument.streaming is True
    assert nvda.quantity == Decimal("2")


async def test_portfolio_ownership_is_enforced_in_the_repository(db, seeded):
    repo = PortfolioRepository(db)
    assert await repo.get(PORTFOLIO_ID, user_id=seeded["user_id"]) is not None
    assert await repo.get(PORTFOLIO_ID, user_id=seeded["other_user_id"]) is None
    assert await repo.get("no-such-portfolio") is None


async def test_streaming_symbols_exclude_funds(db, seeded):
    assert await PortfolioRepository(db).symbols_for_streaming() == ["NVDA"]


async def test_macro_events_are_deduplicated(db, seeded):
    repo = DecisionRepository(db)
    first = await repo.record_macro_event(
        series="DFEDTARU", kind="rate_cut", observed_on=datetime(2026, 12, 9).date(),
        previous_value=5.25, new_value=5.00, change_bps=-25.0, surprise_bps=-10.0,
        triggered_rebalance=True,
    )
    # FRED re-publishes the same print; it must not replay the rebalance.
    duplicate = await repo.record_macro_event(
        series="DFEDTARU", kind="rate_cut", observed_on=datetime(2026, 12, 9).date(),
        previous_value=5.25, new_value=5.00, change_bps=-25.0, surprise_bps=-10.0,
        triggered_rebalance=True,
    )
    assert first is not None
    assert duplicate is None


async def test_confirming_an_unknown_decision_reports_failure(db, seeded):
    assert await DecisionRepository(db).mark_confirmed(PORTFOLIO_ID, "nope") is False
