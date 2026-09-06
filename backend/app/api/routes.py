"""REST endpoints the iOS client calls.

The websocket carries valuation; everything that is a *decision* - the proposed
allocation, the macro events behind it, the confirmation - goes over REST,
because each is a request/response with an audit trail, not a stream.

Response models are the server half of the contract in `mobile/src/types.ts`.
Keep them in step: a field renamed here and not there is a blank screen on a
phone, not a compile error.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Path, Request
from pydantic import BaseModel, Field

from app.api.auth import CurrentUser
from app.db.repositories import AllocationRecord, DecisionRepository, PortfolioRepository

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["portfolio"])

TRADING_DAYS = 252


class AllocationRowOut(BaseModel):
    symbol: str
    name: str
    asset_class: str
    current_weight: float
    target_weight: float
    target_amount: float
    order_amount: float


class AllocationOut(BaseModel):
    portfolio_id: str
    decision_id: str
    decided_at: datetime
    trigger_reason: str
    notional: float
    base_currency: str
    status: str
    applied: bool
    note: str = ""
    expected_return_annual: float
    risk_mad: float
    turnover: float
    binding_constraints: list[str] = Field(default_factory=list)
    rows: list[AllocationRowOut]


class MacroEventOut(BaseModel):
    id: str
    series: str
    kind: str
    observed_on: str
    previous_value: float
    new_value: float
    change_bps: float
    surprise_bps: float
    triggered_rebalance: bool
    note: str = ""


class ConfirmOut(BaseModel):
    accepted: bool
    decision_id: str


def to_allocation_out(record: AllocationRecord) -> AllocationOut:
    return AllocationOut(
        portfolio_id=record.portfolio_id,
        decision_id=record.decision_id,
        decided_at=record.decided_at,
        trigger_reason=record.trigger_reason,
        notional=float(record.notional),
        base_currency=record.base_currency,
        status=record.status,
        applied=record.applied,
        note=record.note,
        # The client shows an annual figure; the model works in daily returns.
        # Converting here keeps that assumption in one place.
        expected_return_annual=record.expected_return_daily * TRADING_DAYS,
        risk_mad=record.risk_mad,
        turnover=record.turnover,
        binding_constraints=record.binding_constraints,
        rows=[
            AllocationRowOut(
                symbol=row.symbol,
                name=row.name,
                asset_class=row.asset_class,
                current_weight=row.current_weight,
                target_weight=row.target_weight,
                target_amount=row.target_amount,
                order_amount=row.order_amount,
            )
            for row in record.rows
        ],
    )


def _decisions(request: Request) -> DecisionRepository:
    repo = getattr(request.app.state, "decisions", None)
    if repo is None:
        # Explicit 503 rather than a 500 traceback: the app is up, this
        # dependency is not wired, and the client should retry later.
        raise HTTPException(status_code=503, detail="Decision store is not configured")
    return repo


def _portfolios(request: Request) -> PortfolioRepository:
    repo = getattr(request.app.state, "portfolios", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Portfolio store is not configured")
    return repo


@router.get("/portfolios/{portfolio_id}/allocation", response_model=AllocationOut)
async def get_allocation(
    request: Request,
    user_id: CurrentUser,
    portfolio_id: Annotated[str, Path()],
) -> AllocationOut:
    """The most recent proposal for this portfolio."""
    record = await _decisions(request).latest(portfolio_id, user_id=user_id)
    if record is None:
        # Same 404 whether the portfolio belongs to someone else or has no
        # decision yet: a distinguishable response leaks which ids exist.
        raise HTTPException(status_code=404, detail="No allocation decision yet")
    return to_allocation_out(record)


@router.get("/macro/events", response_model=list[MacroEventOut])
async def get_macro_events(
    request: Request,
    user_id: CurrentUser,
    limit: int = 20,
) -> list[MacroEventOut]:
    rows = await _decisions(request).macro_events(min(max(limit, 1), 100))
    return [
        MacroEventOut(
            id=row.id,
            series=row.series,
            kind=row.kind,
            observed_on=row.observed_on.isoformat(),
            previous_value=row.previous_value,
            new_value=row.new_value,
            change_bps=row.change_bps,
            surprise_bps=row.surprise_bps,
            triggered_rebalance=row.triggered_rebalance,
            note=row.note,
        )
        for row in rows
    ]


@router.post("/portfolios/{portfolio_id}/rebalance/confirm", response_model=ConfirmOut)
async def confirm_rebalance(
    request: Request,
    user_id: CurrentUser,
    portfolio_id: Annotated[str, Path()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ConfirmOut:
    """Accept a proposal and release its orders.

    The client's `Idempotency-Key` is the decision id.  Replaying it returns the
    original outcome rather than placing a second set of orders - a double tap
    on a train with one bar of signal is the normal case, not the edge case.
    """
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")

    if await _portfolios(request).get(portfolio_id, user_id=user_id) is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    accepted = await _decisions(request).mark_confirmed(portfolio_id, idempotency_key)
    if not accepted:
        raise HTTPException(status_code=404, detail="Unknown decision")

    log.info("confirmed: portfolio=%s decision=%s user=%s", portfolio_id, idempotency_key, user_id)
    return ConfirmOut(accepted=True, decision_id=idempotency_key)
