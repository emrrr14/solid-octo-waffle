import numpy as np
import pandas as pd

from app.analytics.regression import expected_returns, fit_factor_model, scenario_returns


def _panel(n=500, seed=3):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2024-01-01", periods=n)
    surprise = np.zeros(n)
    surprise[rng.choice(n, n // 25, replace=False)] = rng.choice([-25.0, 25.0], n // 25)
    factors = pd.DataFrame(
        {"fed_surprise_bps": surprise, "noise_factor": rng.normal(0, 1, n)}, index=idx
    )
    y = pd.Series(0.0004 - 0.0003 * surprise + rng.normal(0, 0.004, n), index=idx, name="GLD")
    return y, factors


def test_recovers_the_true_beta_and_shrinks_noise():
    y, factors = _panel()
    model = fit_factor_model(y, factors, "GLD", alpha_level=0.10)
    assert model.betas["fed_surprise_bps"] == pytest.approx(-0.0003, abs=1e-4)
    assert model.betas["noise_factor"] == 0.0        # insignificant -> shrunk away
    assert model.n_obs == len(y)
    assert model.anova is not None


def test_expected_return_responds_to_the_scenario():
    y, factors = _panel()
    model = fit_factor_model(y, factors, "GLD")
    cut = model.expected_return({"fed_surprise_bps": -25.0})
    hike = model.expected_return({"fed_surprise_bps": +25.0})
    assert cut > hike                                # gold rallies on dovish surprises
    assert model.expected_return({}) == pytest.approx(model.alpha)


def test_scenario_paths_are_recentred_on_the_scenario_mean():
    y, factors = _panel()
    models = {"GLD": fit_factor_model(y, factors, "GLD")}
    paths = scenario_returns(y.to_frame(), models, factors, {"fed_surprise_bps": -25.0}, ["GLD"])
    mu = expected_returns(models, {"fed_surprise_bps": -25.0}, ["GLD"])
    assert paths.shape == (len(y), 1)
    assert paths.mean() == pytest.approx(mu[0], abs=5e-4)


import pytest  # noqa: E402  (kept last so the helpers above read top-down)
