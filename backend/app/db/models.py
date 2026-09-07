"""SQLAlchemy models for the schema in docs/ARCHITECTURE.md.

Postgres in production, SQLite in tests - so the column types stay portable:
``JSON`` rather than ``JSONB``, ``String`` ids rather than ``UUID``, and
``Numeric`` for anything that is money.  Floats are fine for returns and weights
(they are estimates); they are not fine for balances, where 0.1 + 0.2 eventually
becomes a support ticket.

Ticks are deliberately absent: they belong in a TimescaleDB hypertable written
by the gateway's batch writer, never through the ORM.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    portfolios: Mapped[list["Portfolio"]] = relationship(back_populates="user")


class RefreshToken(Base):
    """One row per issued refresh token; rotation appends, never updates in place.

    ``family_id`` ties a device's chain of rotations together so a detected
    replay can revoke the whole chain rather than one link.
    """

    __tablename__ = "refresh_tokens"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    family_id: Mapped[str] = mapped_column(String(32), index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    device: Mapped[str | None] = mapped_column(String(128), default=None)


class Instrument(Base):
    __tablename__ = "instruments"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    venue: Mapped[str] = mapped_column(String(16))
    currency: Mapped[str] = mapped_column(String(3))
    asset_class: Mapped[str] = mapped_column(String(32))
    streaming: Mapped[bool] = mapped_column(Boolean, default=False)


class Portfolio(Base):
    __tablename__ = "portfolios"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    base_currency: Mapped[str] = mapped_column(String(3), default="TRY")
    cash: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    notional: Mapped[Decimal] = mapped_column(Numeric(18, 2), default=Decimal("0"))
    # The investment mandate: class bands, turnover cap, return floor, objective.
    # Per-portfolio and versioned with the row, because it is the thing a
    # customer agreed to - not a global constant the next deploy can change
    # under them.
    mandate: Mapped[dict] = mapped_column(JSON, default=dict)

    user: Mapped[User] = relationship(back_populates="portfolios")
    positions: Mapped[list["Position"]] = relationship(
        back_populates="portfolio", cascade="all, delete-orphan"
    )


class Position(Base):
    __tablename__ = "positions"
    __table_args__ = (UniqueConstraint("portfolio_id", "symbol", name="uq_position"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolios.id"), index=True)
    symbol: Mapped[str] = mapped_column(ForeignKey("instruments.symbol"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(24, 8))
    avg_cost: Mapped[Decimal] = mapped_column(Numeric(18, 6))

    portfolio: Mapped[Portfolio] = relationship(back_populates="positions")
    instrument: Mapped[Instrument] = relationship()


class FundNav(Base):
    """TEFAS / BES end-of-day NAVs.  The composite PK is what makes the nightly
    ingest idempotent under TEFAS' late and revised publications."""

    __tablename__ = "fund_nav"

    fund_code: Mapped[str] = mapped_column(String(16), primary_key=True)
    nav_date: Mapped[date] = mapped_column(Date, primary_key=True)
    nav: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    fund_type: Mapped[str] = mapped_column(String(3))   # YAT | EMK
    shares: Mapped[Decimal | None] = mapped_column(Numeric(24, 4), default=None)
    investors: Mapped[int | None] = mapped_column(Integer, default=None)


class DailyClose(Base):
    """End-of-day closes for streamed instruments and FX.

    The tick hypertable is where intraday lives; this is the daily series the
    factor model regresses on.  Separate because they have different lifetimes
    (ticks are dropped after 90 days, closes are kept forever) and different
    write patterns (batched firehose vs one row per instrument per day).
    """

    __tablename__ = "daily_close"

    symbol: Mapped[str] = mapped_column(String(32), primary_key=True)
    close_date: Mapped[date] = mapped_column(Date, primary_key=True)
    close: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    currency: Mapped[str] = mapped_column(String(3), default="TRY")


class MacroEventRow(Base):
    __tablename__ = "macro_events"
    __table_args__ = (
        # The natural key of a policy decision: one row per series per print.
        UniqueConstraint("series", "observed_on", "new_value", name="uq_macro_event"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    series: Mapped[str] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    observed_on: Mapped[date] = mapped_column(Date, index=True)
    previous_value: Mapped[float]
    new_value: Mapped[float]
    change_bps: Mapped[float]
    surprise_bps: Mapped[float]
    triggered_rebalance: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class RebalanceDecisionRow(Base):
    """The audit record.  Reproducibility is the product requirement here: when a
    customer asks why their 500 TL moved, this row is the answer, and under SPK
    rules it is the record you must be able to produce."""

    __tablename__ = "rebalance_decisions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    portfolio_id: Mapped[str] = mapped_column(ForeignKey("portfolios.id"), index=True)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    trigger_reason: Mapped[str] = mapped_column(String(256))
    scenario: Mapped[dict] = mapped_column(JSON)
    model_version: Mapped[str] = mapped_column(String(64), default="v1")
    weights_before: Mapped[dict] = mapped_column(JSON)
    weights_after: Mapped[dict] = mapped_column(JSON)
    amounts: Mapped[dict] = mapped_column(JSON)
    orders: Mapped[dict] = mapped_column(JSON)
    expected_return: Mapped[float] = mapped_column(default=0.0)
    risk_mad: Mapped[float] = mapped_column(default=0.0)
    turnover: Mapped[float] = mapped_column(default=0.0)
    binding_constraints: Mapped[list] = mapped_column(JSON, default=list)
    solver_status: Mapped[str] = mapped_column(String(128), default="")
    applied: Mapped[bool] = mapped_column(Boolean, default=False)
    note: Mapped[str] = mapped_column(String(256), default="")
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


Index("ix_decisions_portfolio_time", RebalanceDecisionRow.portfolio_id, RebalanceDecisionRow.decided_at)
