"""Repositories: the only place SQL meets the rest of the app.

Everything above this layer works in domain objects; everything below is
SQLAlchemy.  That boundary is what let the optimiser, the trigger router and the
valuation engine be unit-tested without a database.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.db import models
from app.domain.models import AssetClass, Instrument, Portfolio, Position, Venue
from app.rebalance.engine import RebalanceDecision
from app.security.tokens import (
    TokenSettings,
    hash_password,
    hash_refresh_token,
    new_refresh_token,
    verify_password,
)

log = logging.getLogger(__name__)


class AuthError(Exception):
    """Login or refresh failed.  Deliberately vague at the API boundary."""


@dataclass(slots=True)
class AllocationRecord:
    """Flattened decision, ready for the API layer to serialise."""
    decision_id: str
    portfolio_id: str
    decided_at: datetime
    trigger_reason: str
    notional: Decimal
    base_currency: str
    status: str
    applied: bool
    note: str
    expected_return_daily: float
    risk_mad: float
    turnover: float
    binding_constraints: list[str]
    rows: list["AllocationRecordRow"] = field(default_factory=list)


@dataclass(slots=True)
class AllocationRecordRow:
    symbol: str
    name: str
    asset_class: str
    current_weight: float
    target_weight: float
    target_amount: float
    order_amount: float


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, email: str, password: str) -> models.User:
        user = models.User(email=email.strip().lower(), password_hash=hash_password(password))
        self._session.add(user)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise AuthError("email already registered") from exc
        return user

    async def authenticate(self, email: str, password: str) -> models.User:
        result = await self._session.execute(
            select(models.User).where(models.User.email == email.strip().lower())
        )
        user = result.scalar_one_or_none()

        # Hash even when the user does not exist, so response time does not
        # reveal which emails are registered.
        stored = user.password_hash if user else hash_password("dummy-password")
        if not verify_password(password, stored) or user is None or not user.is_active:
            raise AuthError("invalid credentials")
        return user


class RefreshTokenRepository:
    """Rotating refresh tokens with replay detection."""

    def __init__(self, session: AsyncSession, settings: TokenSettings) -> None:
        self._session = session
        self._settings = settings

    async def issue(
        self, user_id: str, family_id: str | None = None, device: str | None = None
    ) -> str:
        token, token_hash = new_refresh_token()
        self._session.add(
            models.RefreshToken(
                user_id=user_id,
                token_hash=token_hash,
                family_id=family_id or uuid.uuid4().hex,
                expires_at=datetime.now(timezone.utc) + self._settings.refresh_ttl,
                device=device,
            )
        )
        await self._session.flush()
        return token

    async def rotate(self, presented: str) -> tuple[str, str]:
        """Consume a refresh token and mint its successor.

        Returns ``(new_token, user_id)``.  Presenting an already-used token
        revokes the entire family: that is the fingerprint of a stolen token
        being replayed alongside the real device, and the only cheap defence.
        """
        token_hash = hash_refresh_token(presented)
        row = (
            await self._session.execute(
                select(models.RefreshToken).where(models.RefreshToken.token_hash == token_hash)
            )
        ).scalar_one_or_none()

        if row is None:
            raise AuthError("unknown refresh token")

        now = datetime.now(timezone.utc)
        expires_at = row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=timezone.utc)

        if row.used_at is not None:
            log.warning("refresh token replay on family %s - revoking", row.family_id)
            await self.revoke_family(row.family_id)
            raise AuthError("refresh token replayed")
        if row.revoked:
            raise AuthError("refresh token revoked")
        if expires_at <= now:
            raise AuthError("refresh token expired")

        row.used_at = now
        new_token = await self.issue(row.user_id, family_id=row.family_id, device=row.device)
        return new_token, row.user_id

    async def revoke_family(self, family_id: str) -> None:
        await self._session.execute(
            update(models.RefreshToken)
            .where(models.RefreshToken.family_id == family_id)
            .values(revoked=True)
        )

    async def revoke_all_for_user(self, user_id: str) -> None:
        await self._session.execute(
            update(models.RefreshToken)
            .where(models.RefreshToken.user_id == user_id)
            .values(revoked=True)
        )

    async def purge_expired(self, older_than: timedelta = timedelta(days=1)) -> None:
        cutoff = datetime.now(timezone.utc) - older_than
        await self._session.execute(
            update(models.RefreshToken)
            .where(models.RefreshToken.expires_at < cutoff)
            .values(revoked=True)
        )


class PortfolioRepository:
    """Reads portfolios in the shape the valuation engine expects."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def get(self, portfolio_id: str, user_id: str | None = None) -> Portfolio | None:
        async with self._factory() as session:
            row = (
                await session.execute(
                    select(models.Portfolio)
                    .options(
                        selectinload(models.Portfolio.positions).selectinload(
                            models.Position.instrument
                        )
                    )
                    .where(models.Portfolio.id == portfolio_id)
                )
            ).scalar_one_or_none()

            if row is None:
                return None
            # Ownership is checked here, not in the route: every caller of this
            # repository gets the check for free, including the websocket.
            if user_id is not None and row.user_id != user_id:
                return None

            return Portfolio(
                portfolio_id=row.id,
                base_currency=row.base_currency,
                cash=Decimal(row.cash),
                positions=[
                    Position(
                        instrument=Instrument(
                            symbol=p.instrument.symbol,
                            venue=Venue(p.instrument.venue),
                            currency=p.instrument.currency,
                            asset_class=AssetClass(p.instrument.asset_class),
                            streaming=p.instrument.streaming,
                        ),
                        quantity=Decimal(p.quantity),
                        avg_cost=Decimal(p.avg_cost),
                    )
                    for p in row.positions
                ],
            )

    async def load_mandate(self, portfolio_id: str) -> tuple[dict, dict[str, str], Decimal, str] | None:
        """Return ``(mandate_json, class_of, notional, base_currency)``.

        ``class_of`` is read from the instruments table rather than the mandate
        JSON: asset class is a property of the fund, and copying it into every
        customer's mandate is how the same fund ends up in two classes.
        """
        async with self._factory() as session:
            row = (
                await session.execute(
                    select(models.Portfolio).where(models.Portfolio.id == portfolio_id)
                )
            ).scalar_one_or_none()
            if row is None:
                return None

            universe = list((row.mandate or {}).get("universe", []))
            instruments = (
                await session.execute(
                    select(models.Instrument.symbol, models.Instrument.asset_class).where(
                        models.Instrument.symbol.in_(universe)
                    )
                )
            ).all()
            return (
                dict(row.mandate or {}),
                {symbol: asset_class for symbol, asset_class in instruments},
                Decimal(row.notional),
                row.base_currency,
            )

    async def all_ids(self) -> list[str]:
        async with self._factory() as session:
            return list((await session.execute(select(models.Portfolio.id))).scalars())

    async def symbols_for_streaming(self) -> list[str]:
        """The union of streamed symbols - what the gateway subscribes to."""
        async with self._factory() as session:
            result = await session.execute(
                select(models.Instrument.symbol).where(models.Instrument.streaming.is_(True))
            )
            return list(result.scalars())


class DecisionRepository:
    """Rebalance decisions and the macro events behind them."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        self._factory = factory

    async def record(
        self, decision: RebalanceDecision, scenario: dict[str, float], weights_before: dict[str, float]
    ) -> str:
        async with self._factory() as session:
            row = models.RebalanceDecisionRow(
                portfolio_id=decision.portfolio_id,
                decided_at=decision.ts,
                trigger_reason=decision.trigger_reason,
                scenario=scenario,
                weights_before=weights_before,
                weights_after=decision.allocation.weights,
                amounts={k: str(v) for k, v in decision.allocation.amounts.items()},
                orders={k: str(v) for k, v in decision.orders.items()},
                expected_return=decision.allocation.expected_return,
                risk_mad=decision.allocation.risk_mad,
                turnover=decision.allocation.turnover,
                binding_constraints=decision.allocation.binding_constraints,
                solver_status=decision.allocation.status,
                applied=decision.applied,
                note=decision.note,
            )
            session.add(row)
            await session.commit()
            return row.id

    async def latest(self, portfolio_id: str, user_id: str | None = None) -> AllocationRecord | None:
        async with self._factory() as session:
            portfolio = (
                await session.execute(
                    select(models.Portfolio).where(models.Portfolio.id == portfolio_id)
                )
            ).scalar_one_or_none()
            if portfolio is None or (user_id is not None and portfolio.user_id != user_id):
                return None

            row = (
                await session.execute(
                    select(models.RebalanceDecisionRow)
                    .where(models.RebalanceDecisionRow.portfolio_id == portfolio_id)
                    .order_by(models.RebalanceDecisionRow.decided_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if row is None:
                return None

            symbols = sorted(row.weights_after)
            instruments = {
                i.symbol: i
                for i in (
                    await session.execute(
                        select(models.Instrument).where(models.Instrument.symbol.in_(symbols))
                    )
                ).scalars()
            }

            return AllocationRecord(
                decision_id=row.id,
                portfolio_id=row.portfolio_id,
                decided_at=row.decided_at,
                trigger_reason=row.trigger_reason,
                notional=Decimal(portfolio.notional),
                base_currency=portfolio.base_currency,
                status=row.solver_status,
                applied=row.applied,
                note=row.note,
                expected_return_daily=row.expected_return,
                risk_mad=row.risk_mad,
                turnover=row.turnover,
                binding_constraints=list(row.binding_constraints or []),
                rows=[
                    AllocationRecordRow(
                        symbol=symbol,
                        name=instruments[symbol].name if symbol in instruments else symbol,
                        asset_class=(
                            instruments[symbol].asset_class if symbol in instruments else "unclassified"
                        ),
                        current_weight=float(row.weights_before.get(symbol, 0.0)),
                        target_weight=float(row.weights_after[symbol]),
                        target_amount=float(row.amounts.get(symbol, 0)),
                        order_amount=float(row.orders.get(symbol, 0)),
                    )
                    for symbol in symbols
                ],
            )

    async def current_weights(self, portfolio_id: str, universe: list[str]) -> dict[str, float]:
        """Where the money is now.

        Preference order, and the reason for it:
        1. the previous decision's target weights - what the book was last set to;
        2. failing that, positions valued at the latest NAV - the truth when a
           customer has traded outside the advisor;
        3. failing that, zeros - genuinely fresh money, so every constraint but
           turnover applies from a standing start.
        """
        async with self._factory() as session:
            previous = (
                await session.execute(
                    select(models.RebalanceDecisionRow.weights_after)
                    .where(models.RebalanceDecisionRow.portfolio_id == portfolio_id)
                    .order_by(models.RebalanceDecisionRow.decided_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if previous:
                return {symbol: float(previous.get(symbol, 0.0)) for symbol in universe}

            positions = (
                await session.execute(
                    select(models.Position.symbol, models.Position.quantity).where(
                        models.Position.portfolio_id == portfolio_id,
                        models.Position.symbol.in_(universe),
                    )
                )
            ).all()
            if not positions:
                return {symbol: 0.0 for symbol in universe}

            values: dict[str, float] = {}
            for symbol, quantity in positions:
                nav = (
                    await session.execute(
                        select(models.FundNav.nav)
                        .where(models.FundNav.fund_code == symbol)
                        .order_by(models.FundNav.nav_date.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if nav is not None:
                    values[symbol] = float(quantity) * float(nav)

            total = sum(values.values())
            if total <= 0:
                return {symbol: 0.0 for symbol in universe}
            return {symbol: values.get(symbol, 0.0) / total for symbol in universe}

    async def upsert_navs(self, rows: list[dict]) -> int:
        """Idempotent NAV write.

        TEFAS publishes late and revises; the composite primary key plus merge
        means re-running yesterday's ingest is a no-op rather than a duplicate.
        """
        if not rows:
            return 0
        async with self._factory() as session:
            for row in rows:
                await session.merge(models.FundNav(**row))
            await session.commit()
            return len(rows)

    async def mark_confirmed(self, portfolio_id: str, decision_id: str) -> bool:
        """Idempotent: confirming an already-confirmed decision reports success
        without doing anything, so a client retry cannot double-trade."""
        async with self._factory() as session:
            row = (
                await session.execute(
                    select(models.RebalanceDecisionRow).where(
                        models.RebalanceDecisionRow.id == decision_id,
                        models.RebalanceDecisionRow.portfolio_id == portfolio_id,
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                return False
            if row.confirmed_at is not None:
                return True
            row.confirmed_at = datetime.now(timezone.utc)
            await session.commit()
            return True

    async def macro_events(self, limit: int = 20) -> list[models.MacroEventRow]:
        async with self._factory() as session:
            result = await session.execute(
                select(models.MacroEventRow)
                .order_by(models.MacroEventRow.observed_on.desc())
                .limit(limit)
            )
            return list(result.scalars())

    async def record_macro_event(
        self,
        series: str,
        kind: str,
        observed_on: date,
        previous_value: float,
        new_value: float,
        change_bps: float,
        surprise_bps: float,
        triggered_rebalance: bool,
        note: str = "",
    ) -> str | None:
        """Insert once.  The unique constraint absorbs FRED's re-publications,
        so a revised print cannot replay a rebalance."""
        async with self._factory() as session:
            row = models.MacroEventRow(
                series=series,
                kind=kind,
                observed_on=observed_on,
                previous_value=previous_value,
                new_value=new_value,
                change_bps=change_bps,
                surprise_bps=surprise_bps,
                triggered_rebalance=triggered_rebalance,
                note=note,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return None
            return row.id
