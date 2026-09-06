"""End-to-end demo: a 25bp FED cut rebalances a 500 TL sleeve.

Run:  python -m examples.fed_cut_rebalance     (from backend/)

Synthetic data stands in for the TEFAS/BES panel so the whole chain -
detection -> regression/ANOVA -> simplex -> orders - runs offline and
deterministically.  Replace ``build_panel`` with
``TefasClient.history(...) -> nav_to_returns`` and the macro frame with your
FRED/EVDS warehouse tables; nothing else changes.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd

from app.analytics.regression import fit_panel
from app.macro.fed import detect_shift
from app.macro.triggers import TriggerRouter
from app.optimization.simplex import AllocationConstraints, Objective
from app.rebalance.engine import RebalanceEngine

FUNDS = {           # symbol -> asset class
    "AFA": "equity_tr",       # BIST equity fund
    "IPB": "equity_global",   # foreign equity / tech fund
    "TTE": "bond_tr",         # government bond fund
    "GLD": "gold",            # gold fund
    "MMK": "money_market",    # liquid fund
    "EUB": "fx",              # eurobond fund
}

# True sensitivities used to generate the data (per bp of FED surprise).
TRUE_BETAS = {
    "AFA": {"fed_surprise_bps": -0.00022, "d_log_usdtry": -0.35},
    "IPB": {"fed_surprise_bps": -0.00040, "d_log_usdtry": +0.80},
    "TTE": {"fed_surprise_bps": -0.00008, "d_log_usdtry": -0.10},
    "GLD": {"fed_surprise_bps": -0.00030, "d_log_usdtry": +0.90},
    "MMK": {"fed_surprise_bps": +0.00001, "d_log_usdtry": +0.00},
    "EUB": {"fed_surprise_bps": -0.00015, "d_log_usdtry": +0.95},
}
DRIFT = {"AFA": 0.0006, "IPB": 0.0007, "TTE": 0.00035, "GLD": 0.0005, "MMK": 0.00018, "EUB": 0.0004}
NOISE = {"AFA": 0.016, "IPB": 0.014, "TTE": 0.004, "GLD": 0.011, "MMK": 0.0004, "EUB": 0.006}


def build_panel(n_days: int = 750, seed: int = 7) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(end=date(2026, 9, 5), periods=n_days)

    # FOMC-like: mostly zero, occasional 25bp steps, with a priced-in component.
    surprise = np.zeros(n_days)
    meetings = rng.choice(n_days, size=n_days // 40, replace=False)
    surprise[meetings] = rng.choice([-25.0, -12.5, 0.0, 12.5, 25.0], size=len(meetings))
    factors = pd.DataFrame(
        {
            "fed_surprise_bps": surprise,
            "d_log_usdtry": 0.0004 * surprise + rng.normal(0, 0.006, n_days),
        },
        index=idx,
    )

    returns = pd.DataFrame(index=idx)
    for code, betas in TRUE_BETAS.items():
        series = np.full(n_days, DRIFT[code]) + rng.normal(0, NOISE[code], n_days)
        for factor, beta in betas.items():
            series += beta * factors[factor].to_numpy()
        returns[code] = series
    return returns, factors


def main() -> None:
    returns, factors = build_panel()
    symbols = list(FUNDS)

    # 1. Estimate macro sensitivities (HAC OLS + Type-II ANOVA).
    models = fit_panel(returns, factors, alpha_level=0.10)
    print("=== factor model ===")
    for code in symbols:
        m = models[code]
        print(f"{code:>4}  alpha={m.alpha:+.5f}  R2={m.r_squared:5.3f}  n={m.n_obs}  "
              f"beta(fed_surprise)={m.betas['fed_surprise_bps']:+.6f} "
              f"(p={m.pvalues['fed_surprise_bps']:.3g})")
    print("\n=== ANOVA (AFA, Type II) ===")
    print(models["AFA"].anova)

    # 2. Detect the policy shift: 5.50% -> 5.25% when futures priced a hold.
    event = detect_shift(previous=5.50, current=5.25, observed_on=date(2026, 9, 5),
                         expected_rate_pct=5.50)
    trigger = TriggerRouter().evaluate(event)
    assert trigger is not None
    print(f"\n=== trigger ===\n{trigger.reason}\nscenario={trigger.scenario}")

    # 3. Mandate: a balanced 500 TL sleeve.
    constraints = AllocationConstraints(
        class_of=FUNDS,
        class_upper={"equity_tr": 0.35, "equity_global": 0.35, "gold": 0.25, "fx": 0.30},
        class_lower={"money_market": 0.05, "bond_tr": 0.10},
        upper={s: 0.35 for s in symbols},
        turnover_cap=0.60,          # at most 30% of the book changes hands
        min_return=0.00045,         # daily return floor -> ~11%/yr
    )

    current = {"AFA": 0.25, "IPB": 0.20, "TTE": 0.20, "GLD": 0.10, "MMK": 0.10, "EUB": 0.15}
    engine = RebalanceEngine(constraints, objective=Objective.MIN_RISK)
    decision = engine.run(
        portfolio_id="demo-500try",
        trigger=trigger,
        symbols=symbols,
        returns=returns,
        factors=factors,
        models=models,
        current_weights=current,
        notional=Decimal("500.00"),
    )

    print("\n=== allocation (simplex / MAD) ===")
    print(f"status={decision.allocation.status}  applied={decision.applied} {decision.note}")
    print(f"{'fund':>5} {'class':>14} {'old':>7} {'new':>7} {'TRY':>9} {'order':>9}")
    for code in symbols:
        new_w = decision.allocation.weights[code]
        print(f"{code:>5} {FUNDS[code]:>14} {current[code]:>7.1%} {new_w:>7.1%} "
              f"{decision.allocation.amounts[code]:>9} "
              f"{decision.orders.get(code, Decimal('0.00')):>+9}")
    print(f"\nE[r] daily {decision.allocation.expected_return:+.5f} "
          f"(~{decision.allocation.expected_return*252:+.2%}/yr)   "
          f"MAD {decision.allocation.risk_mad:.5f}   "
          f"turnover {decision.allocation.turnover:.1%}")
    print(f"binding constraints: {decision.allocation.binding_constraints}")
    print(f"total allocated: {sum(decision.allocation.amounts.values())} TRY")


if __name__ == "__main__":
    main()
