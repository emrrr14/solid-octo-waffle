"""Macro factor model: OLS + ANOVA over fund / equity returns.

For every instrument we estimate

    r_{i,t} = alpha_i + sum_k beta_{i,k} * f_{k,t} + eps_{i,t}

where ``f`` are macro shocks (FED target change in bp, FED surprise in bp, TR CPI
surprise, USDTRY log-change, DXY, Brent...).  Three things make this usable as
the return input of the optimiser rather than a classroom exercise:

* **HAC (Newey-West) standard errors.**  Daily fund returns are autocorrelated
  and heteroskedastic; plain OLS t-stats overstate significance badly.
* **ANOVA (Type II) for factor groups.**  A single beta's t-test answers "is this
  coefficient non-zero"; ``anova_lm`` answers "does the rates block explain any
  variance at all", which is the question that decides whether a trigger should
  move money.
* **Shrinkage.**  Betas whose p-value exceeds ``alpha`` are set to zero before
  they reach the LP.  An unshrunk factor model hands the Simplex a noise-driven
  expected-return vector and the LP, being a corner-seeking method, will happily
  put 100% of the money on that noise.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import statsmodels.api as sm
from statsmodels.formula.api import ols
from statsmodels.stats.anova import anova_lm

log = logging.getLogger(__name__)


@dataclass(slots=True)
class FactorModel:
    """Estimated macro sensitivities for one instrument."""
    symbol: str
    alpha: float
    betas: dict[str, float]
    pvalues: dict[str, float]
    r_squared: float
    resid_vol: float
    n_obs: int
    anova: pd.DataFrame | None = field(default=None, repr=False)

    def expected_return(self, scenario: dict[str, float], include_alpha: bool = True) -> float:
        """Conditional expected return under a macro shock vector.

        ``scenario`` keys are factor names; missing factors are treated as no
        shock (0), which is the right default - it means "this factor stays at
        its unconditional mean", already absorbed by alpha.
        """
        mu = self.alpha if include_alpha else 0.0
        for factor, beta in self.betas.items():
            mu += beta * scenario.get(factor, 0.0)
        return float(mu)


def _hac_lags(n: int) -> int:
    """Newey-West rule of thumb: floor(4 * (n/100)^(2/9))."""
    return max(1, int(np.floor(4 * (n / 100.0) ** (2.0 / 9.0))))


def fit_factor_model(
    returns: pd.Series,
    factors: pd.DataFrame,
    symbol: str,
    alpha_level: float = 0.10,
    run_anova: bool = True,
) -> FactorModel:
    """Fit one instrument against the macro panel.

    ``returns`` and ``factors`` are aligned on a DatetimeIndex; rows with any NaN
    are dropped (macro series publish on their own calendars).
    """
    df = pd.concat([returns.rename("y"), factors], axis=1).dropna()
    if len(df) < max(30, 5 * factors.shape[1]):
        raise ValueError(
            f"{symbol}: {len(df)} usable observations is too few for "
            f"{factors.shape[1]} factors - widen the window or drop factors"
        )

    y = df["y"].to_numpy(dtype=float)
    X = sm.add_constant(df[factors.columns].to_numpy(dtype=float), has_constant="add")
    fit = sm.OLS(y, X).fit(cov_type="HAC", cov_kwds={"maxlags": _hac_lags(len(df))})

    names = list(factors.columns)
    betas: dict[str, float] = {}
    pvalues: dict[str, float] = {}
    for i, name in enumerate(names, start=1):
        p = float(fit.pvalues[i])
        pvalues[name] = p
        betas[name] = float(fit.params[i]) if p <= alpha_level else 0.0  # shrinkage

    anova_tbl = None
    if run_anova:
        try:
            formula = "y ~ " + " + ".join(f"Q('{c}')" for c in names)
            anova_tbl = anova_lm(ols(formula, data=df).fit(), typ=2)
        except Exception:  # pragma: no cover - ANOVA is diagnostic, never fatal
            log.exception("%s: ANOVA failed, continuing with OLS only", symbol)

    return FactorModel(
        symbol=symbol,
        alpha=float(fit.params[0]),
        betas=betas,
        pvalues=pvalues,
        r_squared=float(fit.rsquared),
        resid_vol=float(np.std(fit.resid, ddof=X.shape[1])),
        n_obs=int(len(df)),
        anova=anova_tbl,
    )


def fit_panel(
    returns: pd.DataFrame,
    factors: pd.DataFrame,
    alpha_level: float = 0.10,
) -> dict[str, FactorModel]:
    """Fit every column of ``returns``; instruments with too little history are skipped."""
    models: dict[str, FactorModel] = {}
    for symbol in returns.columns:
        try:
            models[symbol] = fit_factor_model(returns[symbol], factors, symbol, alpha_level)
        except ValueError as exc:
            log.warning("skipping %s: %s", symbol, exc)
    return models


def expected_returns(
    models: dict[str, FactorModel],
    scenario: dict[str, float],
    symbols: list[str] | None = None,
) -> np.ndarray:
    """mu vector for the optimiser, in the order of ``symbols``."""
    symbols = symbols or list(models)
    return np.array([models[s].expected_return(scenario) for s in symbols], dtype=float)


def scenario_returns(
    returns: pd.DataFrame,
    models: dict[str, FactorModel],
    factors: pd.DataFrame,
    scenario: dict[str, float],
    symbols: list[str],
) -> np.ndarray:
    """Scenario-conditioned return **paths** (T x N) for the MAD risk model.

    Historical residuals are re-centred on the scenario mean: keep the realised
    idiosyncratic co-movement (that is the risk), replace the historical macro
    drift with the one the trigger implies.  This is what lets a FED cut change
    both the expected return *and* the risk geometry the LP sees.
    """
    aligned = pd.concat([returns[symbols], factors], axis=1).dropna()
    paths = np.empty((len(aligned), len(symbols)), dtype=float)
    for j, sym in enumerate(symbols):
        model = models[sym]
        fitted = np.full(len(aligned), model.alpha)
        for factor, beta in model.betas.items():
            if factor in aligned:
                fitted += beta * aligned[factor].to_numpy(dtype=float)
        resid = aligned[sym].to_numpy(dtype=float) - fitted
        paths[:, j] = model.expected_return(scenario) + resid
    return paths
