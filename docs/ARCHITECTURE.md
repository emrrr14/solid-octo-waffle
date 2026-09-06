# Robo-Advisor — System Architecture

A dynamic advisory platform with three clocks running at once, which is the fact
that shapes every decision below:

| Clock | What moves | Where it lives |
|---|---|---|
| **Per second** | NASDAQ / S&P 500 / BIST prices, portfolio valuation | streaming path — websockets, Redis, no database on the hot path |
| **Daily** | TEFAS & BES fund NAVs, factor re-estimation | batch path — scheduled jobs, Postgres/TimescaleDB |
| **Event-driven** | FOMC decisions, CPI prints → rebalancing | trigger path — poller → LP solve → orders |

Mixing them is the single biggest architectural mistake available here. A design
that recomputes optimisation on every tick will burn CPU for nothing (fund NAVs
do not change intraday), and one that values portfolios in a nightly batch has no
product. They are kept as three separate pipelines that share only storage.

---

## 1. Recommended stack

### Backend — Python 3.12, FastAPI, asyncio

Not a preference; a constraint. The analytics *must* be Python or R
(`statsmodels`, `scipy.optimize`, `cvxpy`), and running a Go/Node API next to a
Python quant service means two deployments, a serialisation boundary, and a
model that behaves differently in the service than in the notebook. FastAPI's
async websockets handle thousands of concurrent client connections per pod
because the workload is I/O-bound fan-out, not computation.

Where Python does need help: numerical work goes in NumPy/SciPy (C loops, not
Python loops), and the LP solve runs in a worker process, never in the request
path.

| Concern | Choice | Why this over the alternative |
|---|---|---|
| API + websockets | FastAPI + uvicorn | one language with the quant stack; async fan-out |
| Numerics | NumPy, SciPy (HiGHS), statsmodels | HiGHS ships with SciPy and is a genuine simplex/IPM solver |
| Heavier optimisation (later) | cvxpy + ECOS/OSQP | when you outgrow LP and want real mean-variance QP or CVaR |
| Scheduling | APScheduler (single node) → Celery/Arq + Redis (multi-node) | EOD ingestion, macro polling, rebalancing |
| Hot cache / bus | **Redis 7** | last-value cache + pub/sub fan-out; one component, two jobs |
| Time series | **TimescaleDB** (Postgres extension) | ticks and NAVs as hypertables, continuous aggregates for OHLCV, and *the same database* as the transactional data — one backup, one connection pool, joins across both |
| Transactional | PostgreSQL 16 | users, portfolios, orders, decisions, audit |
| Frontend | **Next.js (React) + TypeScript**, TanStack Query, Zustand | SSR for the first paint, one websocket per tab in a store |
| Charts | TradingView **lightweight-charts** | canvas-based; a React chart library re-rendering at 1 Hz will drop frames |
| Infra | Docker + Kubernetes (or ECS), GitHub Actions | the three process types scale independently |
| Observability | OpenTelemetry → Prometheus + Grafana, Sentry | tick latency and staleness are product metrics, not ops metrics |

**Skip Kafka at the start.** Redis pub/sub carries a retail-scale tick load
(tens of thousands of messages/second) with a fraction of the operational cost.
Move to Kafka/Redpanda only when you need replay, multiple independent consumer
groups, or cross-region durability — the gateway's publish call is the only
place that changes.

### Database schema (the parts that matter)

```sql
-- TimescaleDB hypertable: raw prints, written in batches, never read on the hot path
CREATE TABLE ticks (
  ts          TIMESTAMPTZ  NOT NULL,
  symbol      TEXT         NOT NULL,
  price       NUMERIC(18,6) NOT NULL,
  size        NUMERIC(18,6),
  venue       TEXT         NOT NULL
);
SELECT create_hypertable('ticks', 'ts', chunk_time_interval => INTERVAL '1 day');
SELECT add_retention_policy('ticks', INTERVAL '90 days');   -- keep bars, drop prints

-- 1-minute bars maintained by the database, not by a job you have to babysit
CREATE MATERIALIZED VIEW ohlcv_1m WITH (timescaledb.continuous) AS
SELECT time_bucket('1 minute', ts) AS bucket, symbol,
       first(price, ts) AS open, max(price) AS high,
       min(price) AS low, last(price, ts) AS close, sum(size) AS volume
FROM ticks GROUP BY bucket, symbol;

-- End-of-day NAVs; the PK is what makes the nightly job idempotent under revisions
CREATE TABLE fund_nav (
  fund_code TEXT NOT NULL, nav_date DATE NOT NULL,
  nav NUMERIC(18,6) NOT NULL, fund_type TEXT NOT NULL,   -- 'YAT' | 'EMK'
  shares NUMERIC, investors INTEGER,
  PRIMARY KEY (fund_code, nav_date)
);

-- Every rebalance, reproducible: inputs, model version, solver output, orders
CREATE TABLE rebalance_decision (
  id BIGSERIAL PRIMARY KEY, portfolio_id UUID NOT NULL, decided_at TIMESTAMPTZ NOT NULL,
  trigger JSONB NOT NULL, scenario JSONB NOT NULL, model_version TEXT NOT NULL,
  weights_before JSONB NOT NULL, weights_after JSONB NOT NULL,
  expected_return DOUBLE PRECISION, risk_mad DOUBLE PRECISION,
  binding_constraints TEXT[], solver_status TEXT, orders JSONB NOT NULL
);
```

That last table is not bookkeeping. When a customer asks why their 500 TL left
equities on a Wednesday evening, this row is the answer — and under SPK rules it
is the record you have to be able to produce.

### Process topology

```
                    ┌──────────────────────────────────────────────┐
  vendor WS  ──────▶│ market-gateway   (1 replica per venue)       │
  (Finnhub/          │  normalise → Tick                            │
   Polygon/          │  ├─▶ Redis HSET px:last     (last value)     │
   Foreks)           │  ├─▶ Redis PUBLISH ticks    (fan-out)        │
                    │  └─▶ queue → batch INSERT → TimescaleDB      │
                    └──────────────────────────────────────────────┘
                                   │ Redis
                    ┌──────────────▼───────────────┐   WebSocket   ┌──────────┐
                    │ api  (N replicas, stateless) │◀─────────────▶│ Next.js  │
                    │  1 Hz valuation loop/client  │   1 msg/sec   │ frontend │
                    └──────────────┬───────────────┘               └──────────┘
                                   │
                    ┌──────────────▼───────────────────────────────┐
                    │ worker                                        │
                    │  • TEFAS/BES EOD ingest      (20:00 TRT)      │
                    │  • factor re-fit + ANOVA     (nightly)        │
                    │  • FRED poll → trigger       (every 15 min)   │
                    │  • rebalance LP → orders     (on trigger)     │
                    └───────────────────────────────────────────────┘
```

**One upstream connection per venue, ever.** Vendors price and cap on concurrent
connections and symbol subscriptions, not on your user count. A socket per
browser tab is how a $50/month feed becomes an enterprise contract at 200 users,
and how you hit the connection limit long before that.

---

## 2. Real-time streaming design (task 3)

### Server-paced 1 Hz, not tick-paced

NVDA alone prints thousands of trades per second. Forwarding each one gives the
user no information a once-a-second number doesn't, and melts the browser's main
thread. The API pod runs one loop per connected portfolio that **coalesces**:
each tick it always sends the *latest* state, never a backlog.
See `backend/app/api/ws.py`.

### Snapshot, then deltas

Frame 1 is the full portfolio. After that, only positions whose value actually
moved, plus totals. A 30-position book drops from ~4 KB/s to a few hundred
bytes/s per client — the difference between comfortable and expensive at 10k
concurrent users.

```jsonc
// first frame
{"type":"snapshot","ts":"2026-09-06T13:30:00Z","session":"open","base_currency":"TRY",
 "total_value":170500.00,"day_pnl":3400.00,"day_pnl_pct":2.03,"positions":[...]}
// every second after
{"type":"delta","ts":"2026-09-06T13:30:01Z","session":"open","total_value":170512.40,
 "day_pnl":3412.40,"day_pnl_pct":2.04,
 "positions":[{"symbol":"NVDA","last_price":110.4,"market_value":37536.0,"day_pnl":340.0,"stale":false}]}
```

### Drop, never queue

If a client's socket is slow, skip its frame (`asyncio.wait_for` on the send,
2 s deadline, then disconnect). A buffered backlog of stale valuations is worse
than a gap, and an unbounded send queue is how one bad mobile connection takes
down a pod.

### The market-open behaviour, without special-case code

The loop reads the trading calendar every iteration and picks its own cadence:
`closed → 30 s`, `pre/open → 1 s`. The moment the calendar flips to `open`, the
loop speeds up on its own and the frontend starts ticking. No cron job, no
"market open" event to miss. Use `exchange_calendars` (`XNAS`, `XIST`) in
production so half-days and holidays come from the same source the exchange uses.

Multi-venue books need this per venue: at 13:30 UTC NASDAQ is open and BIST has
closed, so US positions tick and BIST positions carry `stale: true` against
their official close. That flag is what stops the UI from animating a dead price.

### Valuation is server-side

The browser receives values, never the inputs. Prices are licensed data with
per-venue redistribution rules, quantities are private, and a client computing
its own totals drifts from the ledger the moment a partial fill or corporate
action lands.

### Client reconnection

Exponential backoff with jitter, and on reconnect the server sends a fresh
snapshot rather than resuming deltas — the client's state may be arbitrarily old
and reconciling that is more code than re-sending 4 KB.

### Capacity, roughly

One API pod (4 vCPU) sustains ~5,000 concurrent 1 Hz connections; valuation is
O(positions) and dominated by JSON serialisation. Scale on connection count, not
CPU. Redis pub/sub handles 100k+ msg/s on one node — the gateway, not Redis, is
the first bottleneck, and it scales by splitting symbols across venue processes.

---

## 3. Macro-triggered rebalancing (task 2)

Full walkthrough and rationale in [`REBALANCING.md`](REBALANCING.md); the runnable
demo is `backend/examples/fed_cut_rebalance.py`.

```
FRED DFEDTARU poll  →  step change?  →  surprise vs futures  →  cooldown/threshold gate
      →  scenario vector  →  HAC-OLS factor betas (shrunk by p-value)
      →  conditional μ and scenario paths  →  MAD linear program (simplex)
      →  drift gate  →  orders in TRY  →  persisted decision + WS broadcast
```

---

## 4. What will bite you, in order

1. **Licensing and redistribution.** Displaying real-time NASDAQ prices to end
   users is a *redistribution* licence, not a data subscription. Non-display and
   internal-use tiers are far cheaper and do not cover your UI. Budget for it
   before you build the UI around it.
2. **SPK (CMB) authorisation.** Automated portfolio recommendations to retail
   investors in Türkiye are a licensed activity (portföy yönetimi / yatırım
   danışmanlığı). The engineering is the easy half. Design for "advice you
   confirm" from day one — flipping to discretionary later is a rewrite of the
   order path plus a licence.
3. **TEFAS has no contract with you.** `BindHistoryInfo` is the platform's own UI
   endpoint. Fine for a prototype, unacceptable as a production dependency —
   buy a licensed feed (Rasyonet, Foreks, Matriks) and keep the scraper as
   fallback, with a monitor that alerts when the two disagree.
4. **Rebalancing costs real money.** TEFAS funds settle T+1/T+2 with entry/exit
   rules and platform fees; a 25bp FED move that triggers a 40% turnover on
   500 TL can cost more than the alpha it chases. Cooldowns, surprise
   thresholds, turnover caps and minimum ticket sizes are in the code for this
   reason — they are risk controls, not tuning knobs.
5. **Overfitting the macro model.** Two years of daily data and six macro
   factors will produce beautiful, meaningless betas. The p-value shrinkage in
   `regression.py` is the minimum defence; add walk-forward backtesting before
   any of this touches customer money.
