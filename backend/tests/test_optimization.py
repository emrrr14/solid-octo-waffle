import numpy as np
import pytest
from decimal import Decimal

from app.optimization.simplex import (
    AllocationConstraints,
    Objective,
    solve_allocation,
    weights_to_amounts,
)

SYMBOLS = ["EQ", "BOND", "GOLD", "MM"]
CLASS_OF = {"EQ": "equity", "BOND": "bond", "GOLD": "gold", "MM": "money"}


@pytest.fixture
def panel():
    rng = np.random.default_rng(42)
    paths = rng.normal(
        loc=[0.0006, 0.00025, 0.0005, 0.00015],
        scale=[0.018, 0.005, 0.012, 0.0004],
        size=(1500, 4),
    )
    return paths, paths.mean(axis=0)


def test_weights_are_a_simplex_point(panel):
    paths, mu = panel
    sol = solve_allocation(SYMBOLS, mu, paths, AllocationConstraints(class_of=CLASS_OF))
    assert sol.success
    assert sum(sol.weights.values()) == pytest.approx(1.0, abs=1e-9)
    assert all(w >= -1e-9 for w in sol.weights.values())


def test_class_bands_are_respected(panel):
    paths, mu = panel
    c = AllocationConstraints(
        class_of=CLASS_OF,
        class_upper={"equity": 0.30, "gold": 0.15},
        class_lower={"money": 0.10},
    )
    sol = solve_allocation(SYMBOLS, mu, paths, c)
    assert sol.weights["EQ"] <= 0.30 + 1e-6
    assert sol.weights["GOLD"] <= 0.15 + 1e-6
    assert sol.weights["MM"] >= 0.10 - 1e-6


def test_return_floor_binds_and_is_met(panel):
    paths, mu = panel
    floor = float(mu.max()) * 0.5   # above what an unconstrained min-risk book earns
    c = AllocationConstraints(class_of=CLASS_OF, min_return=floor)
    sol = solve_allocation(SYMBOLS, mu, paths, c)
    assert sol.expected_return >= floor - 1e-9
    assert "return_floor" in sol.binding


def test_min_risk_beats_max_return_on_risk(panel):
    paths, mu = panel
    low = solve_allocation(SYMBOLS, mu, paths, AllocationConstraints(class_of=CLASS_OF))
    high = solve_allocation(
        SYMBOLS, mu, paths,
        AllocationConstraints(class_of=CLASS_OF, max_risk=1.0),
        objective=Objective.MAX_RETURN,
    )
    assert low.risk_mad <= high.risk_mad + 1e-12
    assert high.expected_return >= low.expected_return - 1e-12


def test_turnover_cap_limits_trading(panel):
    paths, mu = panel
    current = {"EQ": 0.25, "BOND": 0.25, "GOLD": 0.25, "MM": 0.25}
    c = AllocationConstraints(class_of=CLASS_OF, turnover_cap=0.20)
    sol = solve_allocation(SYMBOLS, mu, paths, c, current_weights=current)
    assert sol.turnover <= 0.20 + 1e-6
    assert "turnover_budget" in sol.binding


def test_infeasible_mandate_reports_failure(panel):
    paths, mu = panel
    c = AllocationConstraints(
        class_of=CLASS_OF,
        class_upper={"equity": 0.1, "bond": 0.1, "gold": 0.1, "money": 0.1},
    )
    sol = solve_allocation(SYMBOLS, mu, paths, c)
    assert not sol.success
    assert sol.weights == {}


def test_amounts_sum_exactly_to_notional():
    weights = {"A": 1 / 3, "B": 1 / 3, "C": 1 / 3}
    amounts = weights_to_amounts(weights, Decimal("500.00"))
    assert sum(amounts.values()) == Decimal("500.00")
    assert all(a.as_tuple().exponent == -2 for a in amounts.values())
