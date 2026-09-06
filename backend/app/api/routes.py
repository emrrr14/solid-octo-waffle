"""REST endpoints the iOS client calls.

The websocket carries valuation; everything that is a *decision* - the proposed
allocation, the macro events behind it, the confirmation - goes over REST, because
each is a request/response with an audit trail, not a stream.

Response models are the server half of the contract in `mobile/src/types.ts`.
Keep them in step: a field renamed here and not there is a blank screen on a
phone, not a compile error.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Annotated, Protocol

from fastapi import APIRouter, Header, HTTPException, Path, Request
from pydantic import BaseModel, Field

from app.rebalance.engine import RebalanceDecision

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


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


class DecisionStore(Protocol):
    async def latest(self, portfolio_id: str) -> RebalanceDecision | None: ...
    async def macro_events(self, limit: int) -> list[MacroEventOut]: ...
    async def mark_confirmed(self, portfolio_id: str, decision_id: str) -> bool: ...


def to_allocation_out(
    decision: RebalanceDecision,
    notional: float,
    base_currency: str,
    names: dict[str, str],
    classes: dict[str, str],
    current_weights: dict[str, float],
) -> AllocationOut:
    allocation = decision.allocation
    return AllocationOut(
        portfolio_id=decision.portfolio_id,
        decided_at=decision.ts,
        trigger_reason=decision.trigger_reason,
        notional=notional,
        base_currency=base_currency,
        status=allocation.status,
        applied=decision.applied,
        note=decision.note,
        # The client shows an annual figure; the model works in daily returns.
        # Converting here keeps that assumption in one place.
        expected_return_annual=allocation.expected_return * 252,
        risk_mad=allocation.risk_mad,
        turnover=allocation.turnover,
        binding_constraints=allocation.binding_constraints,
        rows=[
            AllocationRowOut(
                symbol=symbol,
                name=names.get(symbol, symbol),
                asset_class=classes.get(symbol, "unclassified"),
                current_weight=current_weights.get(symbol, 0.0),
                target_weight=weight,
                target_amount=float(allocation.amounts[symbol]),
                order_amount=float(decision.orders.get(symbol, 0)),
            )
            for symbol, weight in allocation.weights.items()
        ],
    )


def _store(request: Request) -> DecisionStore:
    store = getattr(request.app.state, "decisions", None)
    if store is None:
        # Explicit 503 rather than a 500 traceback: the app is running, this
        # dependency is not wired yet, and the client should retry later.
        raise HTTPException(status_code=503, detail="Decision store is not configured")
    return store


@router.get("/portfolios/{portfolio_id}/allocation", response_model=AllocationOut)
async def get_allocation(
    request: Request,
    portfolio_id: Annotated[str, Path()],
) -> AllocationOut:
    """The most recent proposal for this portfolio."""
    decision = await _store(request).latest(portfolio_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="No allocation decision yet")
    meta = request.app.state.portfolio_meta[portfolio_id]
    return to_allocation_out(
        decision,
        notional=meta["notional"],
        base_currency=meta["base_currency"],
        names=meta["names"],
        classes=meta["classes"],
        current_weights=meta["current_weights"],
    )


@router.get("/macro/events", response_model=list[MacroEventOut])
async def get_macro_events(request: Request, limit: int = 20) -> list[MacroEventOut]:
    return await _store(request).macro_events(min(limit, 100))


@router.post("/portfolios/{portfolio_id}/rebalance/confirm", response_model=ConfirmOut)
async def confirm_rebalance(
    request: Request,
    portfolio_id: Annotated[str, Path()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> ConfirmOut:
    """Accept a proposal and release its orders.

    The client's `Idempotency-Key` is the decision id. Replaying it must return
    the original outcome rather than placing a second set of orders - a double
    tap on a train with one bar of signal is the normal case, not the edge case.
    """
    if not idempotency_key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    log.info(
        "confirm requested: portfolio=%s decision=%s at %s",
        portfolio_id, idempotency_key, datetime.now(timezone.utc).isoformat(),
    )
    accepted = await _store(request).mark_confirmed(portfolio_id, idempotency_key)
    return ConfirmOut(accepted=accepted, decision_id=idempotency_key)
