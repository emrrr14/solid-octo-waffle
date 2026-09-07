"""Portfolio allocation as a linear program, solved with the simplex method.

Why an LP and not Markowitz
---------------------------
Classical mean-variance is a *quadratic* program - ``w' Sigma w`` cannot be fed
to the simplex algorithm.  The operations-research answer is the Konno-Yamazaki
(1991) **mean absolute deviation** model, which measures risk as

    MAD(w) = (1/T) * sum_t | sum_i (r_{t,i} - mu_i) * w_i |

and linearises the absolute value with one auxiliary variable per period.  MAD
and variance rank portfolios identically under elliptical returns, MAD is more
robust under the fat tails that macro shocks actually produce, and the whole
problem becomes an LP the simplex method solves exactly, with duals that tell
you which constraint is costing you return.

Decision vector
---------------
    x = [ w_1..w_N | y_1..y_T | t_1..t_N ]
        weights      MAD aux     turnover aux (|w_i - w0_i|)

Constraints
-----------
    sum_i w_i = 1                                     (fully invested)
    lb_i <= w_i <= ub_i                               (per-instrument mandate)
    L_c <= sum_{i in c} w_i <= U_c                    (per-asset-class bands)
    y_t >= +sum_i (r_{t,i} - mu_i) w_i                (MAD linearisation)
    y_t >= -sum_i (r_{t,i} - mu_i) w_i
    t_i >= +(w_i - w0_i),  t_i >= -(w_i - w0_i)       (turnover linearisation)
    sum_i t_i <= turnover_cap                         (trading friction budget)

Objective
---------
    MIN_RISK   : min (1/T) sum_t y_t     s.t.  mu' w >= min_return
    MAX_RETURN : max mu' w               s.t.  (1/T) sum_t y_t <= max_risk

Both are pure LPs.  ``method="highs-ds"`` is HiGHS' dual simplex; pass
``method="highs-ipm"`` for the interior-point solver on very large panels.
"""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from decimal import ROUND_DOWN, Decimal

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import csr_matrix

log = logging.getLogger(__name__)


class Objective(str, enum.Enum):
    MIN_RISK = "min_risk"
    MAX_RETURN = "max_return"


@dataclass(slots=True)
class AllocationConstraints:
    """Everything the mandate, the regulator and the fund platform impose."""
    lower: dict[str, float] = field(default_factory=dict)          # per symbol, default 0
    upper: dict[str, float] = field(default_factory=dict)          # per symbol, default 1
    class_of: dict[str, str] = field(default_factory=dict)         # symbol -> asset class
    class_lower: dict[str, float] = field(default_factory=dict)    # class -> floor
    class_upper: dict[str, float] = field(default_factory=dict)    # class -> cap
    turnover_cap: float = 2.0                                      # sum |dw|; 2.0 = unconstrained
    min_return: float | None = None                                # MIN_RISK mode
    max_risk: float | None = None                                  # MAX_RETURN mode


@dataclass(slots=True)
class LPSolution:
    weights: dict[str, float]
    expected_return: float
    risk_mad: float
    turnover: float
    status: str
    success: bool
    binding: list[str] = field(default_factory=list)
    shadow_prices: dict[str, float] = field(default_factory=dict)


def solve_allocation(
    symbols: list[str],
    mu: np.ndarray,
    scenario_paths: np.ndarray,
    constraints: AllocationConstraints,
    current_weights: dict[str, float] | None = None,
    objective: Objective = Objective.MIN_RISK,
    method: str = "highs-ds",
) -> LPSolution:
    """Solve the allocation LP.

    Parameters
    ----------
    symbols : instrument order for every vector/matrix here.
    mu : (N,) expected returns, per period, from the macro factor model.
    scenario_paths : (T, N) scenario-conditioned return paths for the MAD term.
    current_weights : w0 for the turnover constraint; absent -> 0 (fresh money).
    """
    n = len(symbols)
    if mu.shape != (n,):
        raise ValueError(f"mu has shape {mu.shape}, expected ({n},)")
    if scenario_paths.ndim != 2 or scenario_paths.shape[1] != n:
        raise ValueError(f"scenario_paths has shape {scenario_paths.shape}, expected (T, {n})")

    T = scenario_paths.shape[0]
    w0 = np.array([(current_weights or {}).get(s, 0.0) for s in symbols], dtype=float)

    # Deviations from the scenario mean - the matrix inside the absolute value.
    D = scenario_paths - scenario_paths.mean(axis=0, keepdims=True)   # (T, N)

    n_var = n + T + n
    sl_w = slice(0, n)
    sl_y = slice(n, n + T)
    sl_t = slice(n + T, n_var)

    # ---- objective -----------------------------------------------------
    c = np.zeros(n_var)
    if objective is Objective.MIN_RISK:
        c[sl_y] = 1.0 / T
    else:
        c[sl_w] = -mu                       # linprog minimises

    # ---- inequality block  A_ub x <= b_ub -------------------------------
    rows: list[np.ndarray] = []
    rhs: list[float] = []
    labels: list[str] = []

    for t in range(T):                      # MAD:  +/- D_t w - y_t <= 0
        for sign in (1.0, -1.0):
            row = np.zeros(n_var)
            row[sl_w] = sign * D[t]
            row[n + t] = -1.0
            rows.append(row)
            rhs.append(0.0)
            labels.append(f"mad[{t}]{'+' if sign > 0 else '-'}")

    for i in range(n):                      # turnover:  +/- (w_i - w0_i) - t_i <= 0
        for sign in (1.0, -1.0):
            row = np.zeros(n_var)
            row[i] = sign
            row[n + T + i] = -1.0
            rows.append(row)
            rhs.append(sign * w0[i])
            labels.append(f"turnover[{symbols[i]}]{'+' if sign > 0 else '-'}")

    # Becoming fully invested from a book that holds `w0` requires at least
    # |1 - sum(w0)| of trading, so a tighter budget than that is arithmetically
    # unsatisfiable - most often for fresh money, where w0 is all zeros and the
    # LP would otherwise come back infeasible for a mandate that is perfectly
    # sensible. Widen to the floor and say so, rather than refusing to invest.
    min_turnover = abs(1.0 - float(w0.sum()))
    effective_cap = constraints.turnover_cap
    if effective_cap < min_turnover - 1e-12:
        log.info(
            "turnover cap %.2f is below the %.2f needed to reach a fully invested "
            "book from the current holdings; using %.2f",
            constraints.turnover_cap, min_turnover, min_turnover,
        )
        effective_cap = min_turnover

    row = np.zeros(n_var)                   # sum t_i <= cap
    row[sl_t] = 1.0
    rows.append(row)
    rhs.append(effective_cap)
    labels.append("turnover_budget")

    classes = sorted({constraints.class_of.get(s, "unclassified") for s in symbols})
    for cls in classes:
        members = [i for i, s in enumerate(symbols) if constraints.class_of.get(s, "unclassified") == cls]
        if not members:
            continue
        if cls in constraints.class_upper:
            row = np.zeros(n_var)
            row[members] = 1.0
            rows.append(row)
            rhs.append(constraints.class_upper[cls])
            labels.append(f"class_upper[{cls}]")
        if cls in constraints.class_lower:
            row = np.zeros(n_var)
            row[members] = -1.0
            rows.append(row)
            rhs.append(-constraints.class_lower[cls])
            labels.append(f"class_lower[{cls}]")

    if objective is Objective.MIN_RISK and constraints.min_return is not None:
        row = np.zeros(n_var)               # -mu' w <= -min_return
        row[sl_w] = -mu
        rows.append(row)
        rhs.append(-constraints.min_return)
        labels.append("return_floor")
    if objective is Objective.MAX_RETURN and constraints.max_risk is not None:
        row = np.zeros(n_var)               # (1/T) sum y <= max_risk
        row[sl_y] = 1.0 / T
        rows.append(row)
        rhs.append(constraints.max_risk)
        labels.append("risk_cap")

    A_ub = csr_matrix(np.vstack(rows))
    b_ub = np.array(rhs, dtype=float)

    # ---- equality: fully invested ---------------------------------------
    A_eq = np.zeros((1, n_var))
    A_eq[0, sl_w] = 1.0
    b_eq = np.array([1.0])

    bounds = (
        [(constraints.lower.get(s, 0.0), constraints.upper.get(s, 1.0)) for s in symbols]
        + [(0.0, None)] * T
        + [(0.0, None)] * n
    )

    res = linprog(c, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=bounds, method=method)

    if not res.success:
        log.error("allocation LP infeasible: %s", res.message)
        return LPSolution({}, 0.0, 0.0, 0.0, res.message, False)

    w = np.asarray(res.x[sl_w], dtype=float)
    w = np.clip(w, 0.0, None)
    w = w / w.sum()                          # scrub solver-level rounding dust

    binding: list[str] = []
    shadow: dict[str, float] = {}
    marginals = getattr(getattr(res, "ineqlin", None), "marginals", None)
    if marginals is not None:
        for label, dual in zip(labels, np.asarray(marginals)):
            if abs(dual) > 1e-9 and not label.startswith(("mad[", "turnover[")):
                binding.append(label)
                shadow[label] = float(dual)

    return LPSolution(
        weights={s: float(wi) for s, wi in zip(symbols, w)},
        expected_return=float(mu @ w),
        risk_mad=float(np.mean(np.abs(D @ w))),
        turnover=float(np.abs(w - w0).sum()),
        status=res.message,
        success=True,
        binding=binding,
        shadow_prices=shadow,
    )


def weights_to_amounts(
    weights: dict[str, float],
    notional: Decimal,
    quantum: Decimal = Decimal("0.01"),
) -> dict[str, Decimal]:
    """Turn weights into money that sums *exactly* to ``notional``.

    Naive rounding of 500.00 TL across six funds leaves a few kurus stranded;
    the largest-remainder method distributes them deterministically so the
    ledger always balances.
    """
    raw = {s: (notional * Decimal(str(w))) for s, w in weights.items()}
    floored = {s: v.quantize(quantum, rounding=ROUND_DOWN) for s, v in raw.items()}
    residual = notional - sum(floored.values())
    steps = int((residual / quantum).to_integral_value())
    for symbol, _ in sorted(raw.items(), key=lambda kv: kv[1] - floored[kv[0]], reverse=True)[:steps]:
        floored[symbol] += quantum
    return floored
