"""The rebalance pipeline: trigger -> factor model -> LP -> orders.

    FED cut detected
        -> scenario vector            (app.macro.triggers.build_scenario)
        -> conditional mu and paths   (app.analytics.regression)
        -> allocation LP / simplex    (app.optimization.simplex)
        -> drift gate
        -> orders in TRY              (weights_to_amounts + TEFAS lot rules)
        -> persisted decision + WS broadcast

Every step is a pure function of its inputs; the only I/O is at the edges.  That
is what makes the decision reproducible - and reproducibility is not an
engineering nicety here, it is what you show a regulator or a customer who asks
why their 500 TL moved out of equities on a Wednesday evening.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd

from app.analytics.regression import FactorModel, expected_returns, scenario_returns
from app.domain.models import AllocationResult, RebalanceTrigger
from app.optimization.simplex import (
    AllocationConstraints,
    Objective,
    solve_allocation,
    weights_to_amounts,
)

log = logging.getLogger(__name__)


@dataclass(slots=True)
class RebalanceDecision:
    portfolio_id: str
    ts: datetime
    trigger_reason: str
    allocation: AllocationResult
    orders: dict[str, Decimal]        # symbol -> signed TRY amount (+ buy, - sell)
    applied: bool
    note: str = ""


class RebalanceEngine:
    def __init__(
        self,
        constraints: AllocationConstraints,
        objective: Objective = Objective.MIN_RISK,
        min_order_try: Decimal = Decimal("10.00"),
    ) -> None:
        self._constraints = constraints
        self._objective = objective
        self._min_order = min_order_try

    def run(
        self,
        portfolio_id: str,
        trigger: RebalanceTrigger,
        symbols: list[str],
        returns: pd.DataFrame,
        factors: pd.DataFrame,
        models: dict[str, FactorModel],
        current_weights: dict[str, float],
        notional: Decimal,
        min_drift: float = 0.02,
    ) -> RebalanceDecision:
        now = datetime.now(timezone.utc)

        mu = expected_returns(models, trigger.scenario, symbols)
        paths = scenario_returns(returns, models, factors, trigger.scenario, symbols)

        sol = solve_allocation(
            symbols=symbols,
            mu=mu,
            scenario_paths=paths,
            constraints=self._constraints,
            current_weights=current_weights,
            objective=self._objective,
        )

        if not sol.success:
            # Infeasible mandate: hold the book, alert, never guess.
            empty = AllocationResult({}, {}, 0.0, 0.0, 0.0, [], sol.status)
            return RebalanceDecision(portfolio_id, now, trigger.reason, empty, {}, False,
                                     f"LP infeasible: {sol.status}")

        drift = sum(abs(sol.weights.get(s, 0.0) - current_weights.get(s, 0.0)) for s in symbols) / 2.0
        target_amounts = weights_to_amounts(sol.weights, notional)
        current_amounts = weights_to_amounts(
            {s: current_weights.get(s, 0.0) for s in symbols}, notional
        ) if current_weights else {s: Decimal("0.00") for s in symbols}

        orders = {
            s: (target_amounts[s] - current_amounts.get(s, Decimal("0.00")))
            for s in symbols
        }
        orders = {s: v for s, v in orders.items() if abs(v) >= self._min_order}

        allocation = AllocationResult(
            weights=sol.weights,
            amounts=target_amounts,
            expected_return=sol.expected_return,
            risk_mad=sol.risk_mad,
            turnover=sol.turnover,
            binding_constraints=sol.binding,
            status=sol.status,
        )

        if drift < min_drift:
            return RebalanceDecision(portfolio_id, now, trigger.reason, allocation, {}, False,
                                     f"drift {drift:.3f} below {min_drift:.3f} - holding")
        if not orders:
            return RebalanceDecision(portfolio_id, now, trigger.reason, allocation, {}, False,
                                     "all orders below minimum ticket size")

        log.info(
            "rebalance %s: %s -> turnover %.1f%%, %d orders, binding=%s",
            portfolio_id, trigger.reason, sol.turnover * 100, len(orders), sol.binding,
        )
        return RebalanceDecision(portfolio_id, now, trigger.reason, allocation, orders, True)
