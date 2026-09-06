# Macro-triggered rebalancing

How 500 TL moves when the FED cuts. Every step below is code in this repo, and
`python -m examples.fed_cut_rebalance` runs the whole chain offline.

```
FRED poll → step detection → surprise → governance gate
   → scenario vector → HAC-OLS betas (p-value shrinkage) → conditional μ, paths
   → MAD linear program (simplex) → drift gate → orders in TRY → audit row
```

## 1. Detecting the shift — `app/macro/fed.py`

Watch **`DFEDTARU`** (target range upper limit) on FRED, not `EFFR`. The target
is a step function, so "a shift happened" is exactly "today's value differs from
the stored value". EFFR drifts intraday and would fire constantly.

Two properties matter more than the HTTP call:

* **Idempotency** — FRED revises and re-publishes. State is keyed on the last
  observed level, so the same decision cannot fire two rebalances. Cold start
  adopts the current level silently and never fires on boot.
* **Surprise, not level** — a 25bp cut that futures had fully priced moves
  nothing; a 25bp cut against an expected hold moves everything.
  `surprise_bps = (actual − market-implied) × 100`, sourced from fed funds
  futures, falling back to the raw change when no expectation is available.

## 2. Governance — `app/macro/triggers.py`

Three gates stand between a headline and a trade, because trading is not free:

| Gate | Default | Prevents |
|---|---|---|
| `min_surprise_bps` | 10bp | churning on fully-priced moves |
| `cooldown` | 6h | a revised print replaying the same decision |
| `min_weight_drift` | 2% | paying fund entry/exit fees to move 8 TL |
| `min_order_try` | 10 TL | orders below any sane ticket size |

`build_scenario` is the *single* translation from a world event to regressor
units, so the regression and the LP can never disagree about what a shock means.

## 3. Estimating sensitivity — `app/analytics/regression.py`

For each fund: `r_it = α_i + Σ_k β_ik f_kt + ε_it`, with

* **HAC (Newey–West) standard errors** — daily fund returns are autocorrelated
  and heteroskedastic; plain OLS t-stats overstate significance badly.
* **Type-II ANOVA** (`anova_lm`) — a t-test asks "is this coefficient non-zero";
  ANOVA asks "does the rates block explain any variance at all", which is the
  question that decides whether a trigger should move money.
* **Shrinkage** — betas with p > 0.10 are set to zero *before* the LP sees them.
  This is the most important line in the file: an LP is a corner-seeking method,
  so an unshrunk noise beta doesn't tilt the portfolio, it takes it over.

`scenario_returns` builds the T×N paths the risk model needs by keeping the
historical residuals (that is the genuine co-movement) and re-centring them on
the scenario mean. So a FED cut changes both the expected return *and* the risk
geometry the optimiser sees.

## 4. The optimisation — `app/optimization/simplex.py`

Markowitz mean-variance is a **quadratic** program: `w'Σw` cannot go to a simplex
solver. The operations-research answer is Konno–Yamazaki (1991) **mean absolute
deviation**:

```
MAD(w) = (1/T) Σ_t | Σ_i (r_ti − μ_i) w_i |
```

Linearise the absolute value with one auxiliary variable per period and the whole
problem is an LP. MAD ranks portfolios identically to variance under elliptical
returns, is more robust under the fat tails macro shocks actually produce, and
gives you **dual prices** — which constraint is costing you return, in return
units.

**Decision vector** `x = [w₁..w_N | y₁..y_T | t₁..t_N]` (weights, MAD aux,
turnover aux).

```
minimise   (1/T) Σ_t y_t                      [MIN_RISK]      or   maximise μ'w  [MAX_RETURN]
s.t.       Σ_i w_i = 1                        fully invested
           lb_i ≤ w_i ≤ ub_i                  per-instrument mandate
           L_c ≤ Σ_{i∈c} w_i ≤ U_c            asset-class bands (equity/bond/gold/FX/MM)
           y_t ≥ ±Σ_i (r_ti − μ_i) w_i        MAD linearisation
           t_i ≥ ±(w_i − w⁰_i)                turnover linearisation
           Σ_i t_i ≤ turnover_cap             trading-friction budget
           μ'w ≥ min_return                   return floor  (MIN_RISK)
           (1/T) Σ_t y_t ≤ max_risk           risk cap      (MAX_RETURN)
```

Solved by `scipy.optimize.linprog(method="highs-ds")` — HiGHS' **dual simplex**.
Switch to `"highs-ipm"` (interior point) if T×N grows past a few hundred thousand
rows; the formulation is unchanged.

The turnover constraint is what makes this a *re*balancer rather than an
optimiser that happens to run twice. Without it the LP jumps to a new corner on
every trigger and the fees eat the alpha.

`weights_to_amounts` converts weights to money with the largest-remainder
method, so the orders sum to **exactly** 500.00 TL — naive rounding strands
kuruş and the ledger stops balancing.

## 5. Sample run

```
$ cd backend && python -m examples.fed_cut_rebalance

=== factor model ===
 AFA  alpha=+0.00072  R2=0.012  beta(fed_surprise)=+0.000000 (p=0.635)     ← shrunk to zero
 GLD  alpha=+0.00074  R2=0.177  beta(fed_surprise)=-0.000404 (p=0.00328)
 EUB  alpha=+0.00042  R2=0.434  beta(fed_surprise)=-0.000283 (p=0.00112)

=== trigger ===
rate_cut -25bp on 2026-09-05 (-25bp vs expectation)
scenario={'d_fed_bps': -25.0, 'fed_surprise_bps': -25.0, 'd_log_usdtry': -0.01}

=== allocation (simplex / MAD) ===
 fund          class     old     new       TRY     order
  AFA      equity_tr   25.0%    6.1%     30.65    -94.35
  IPB  equity_global   20.0%    8.9%     44.35    -55.65
  TTE        bond_tr   20.0%   25.0%    125.00    +25.00
  GLD           gold   10.0%   10.0%     50.00     +0.00
  MMK   money_market   10.0%   35.0%    175.00   +125.00
  EUB             fx   15.0%   15.0%     75.00     +0.00

binding constraints: ['turnover_budget']        ← the cap, not the model, set this book
total allocated: 500.00 TRY
```

Read `binding_constraints` on every decision. When `turnover_budget` binds, the
LP wanted to move further and your friction budget stopped it — that is the
constraint to argue about, and the dual price tells you what relaxing it is worth.

## 6. Before this touches customer money

* **Walk-forward backtest.** Re-fit on a rolling window, rebalance only on
  triggers as they historically occurred, and charge real TEFAS fees and T+1/T+2
  settlement. A model tested in-sample on two years of daily data will look
  excellent and mean nothing.
* **Shadow mode.** Run the engine live for a quarter writing `rebalance_decision`
  rows without sending orders, then compare against what you would have wanted.
* **Kill switch.** One config flag that stops all automated rebalancing while
  leaving valuation running.
* **Regulatory posture.** In Türkiye, automated portfolio recommendations to
  retail investors are SPK-licensed activity. Ship "advice you confirm" first;
  discretionary execution is a licence plus an order-path rewrite.
