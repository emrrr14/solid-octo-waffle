# Robo-Advisor — reference architecture and core engine

A design and a working core for a real-time budget/investment advisor:
per-second portfolio valuation over NASDAQ/S&P 500/BIST, daily TEFAS & BES fund
data, and macro-triggered (FED, CPI) rebalancing driven by a regression factor
model and a linear program solved with the simplex method.

## Documentation

| Document | Answers |
|---|---|
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Stack choices, process topology, database schema, **websocket streaming design**, capacity, and what will bite you |
| [`docs/DATA_PROVIDERS.md`](docs/DATA_PROVIDERS.md) | Which market-data, fund, macro and FX providers to use, and the licensing traps |
| [`docs/REBALANCING.md`](docs/REBALANCING.md) | The FED-trigger → regression/ANOVA → simplex → orders pipeline, with the maths |

## Code

```
backend/app/
  domain/models.py           shared types (Tick, Portfolio, MacroEvent, ...)
  market/
    providers.py             Finnhub WS adapter + REST polling fallback (one socket per venue)
    gateway.py               upstream → Redis last-value cache + pub/sub + batched history
    cache.py                 hot-path last-value cache
    session.py               NASDAQ/BIST trading-session state
    valuation.py             server-side portfolio valuation with FX and staleness
  api/ws.py                  1 Hz snapshot+delta push to the frontend
  macro/
    fed.py                   FRED DFEDTARU step detection, idempotent, surprise-aware
    triggers.py              scenario mapping + cooldown/threshold governance
  analytics/regression.py    HAC-OLS factor model + Type-II ANOVA + p-value shrinkage
  optimization/simplex.py    Konno–Yamazaki MAD linear program (HiGHS dual simplex)
  rebalance/engine.py        trigger → μ → LP → drift gate → orders
  tefas/client.py            TEFAS (YAT) and BES (EMK) end-of-day ingestion
  examples/fed_cut_rebalance.py   runnable end-to-end demo
R/macro_factor_model.R       the same regression/ANOVA/LP in R, as a validation twin
```

## Run

```bash
cd backend
pip install -r requirements.txt

python -m examples.fed_cut_rebalance   # end-to-end: FED cut → ANOVA → simplex → 500 TL orders
pytest -q                              # 21 tests: LP constraints, triggers, valuation, betas
```

The demo and the tests run offline on synthetic data — no API keys needed.
Swap `build_panel()` for `TefasClient.history(...)` and the FRED poller for real
credentials to run it against live data.

## Status

Core business logic, tests and architecture are complete and runnable. Not yet
built (deliberately — they are wiring, not design): persistence layer
(SQLAlchemy models for the schema in `ARCHITECTURE.md`), authentication, the
Next.js frontend, and broker/fund order execution.
