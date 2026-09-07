# Running it

Three processes, one database, one Redis. They scale independently and fail
independently — which is the point of splitting them.

| Process | Command | Replicas |
|---|---|---|
| API | `uvicorn app.main:app` | N — stateless, scale on websocket connections |
| Worker | `python -m app.worker.main` | 1 (locks make a rolling deploy safe, not a fleet) |
| Market gateway | `python -m app.market.gateway` (per venue) | 1 **per venue**, never more |

## Environment

| Variable | Required | Notes |
|---|---|---|
| `DATABASE_URL` | yes | `postgresql+asyncpg://…`; dev defaults to SQLite |
| `REDIS_URL` | yes | last-value cache, tick fan-out, job locks |
| `JWT_SECRET` | yes | ≥32 chars. Startup **fails** without it unless `DEV_MODE=1` |
| `FRED_API_KEY` | for triggers | absent → the FED poller logs a warning and stays off |
| `FINNHUB_TOKEN` | for streaming | the gateway's upstream credential |
| `ACCESS_TTL_MINUTES` / `REFRESH_TTL_DAYS` | no | default 15 / 30 |
| `DEV_MODE` | no | throwaway JWT key, auto-migrate on boot |

Rotating `JWT_SECRET` invalidates every access token immediately; refresh tokens
survive, so clients recover on their next refresh. That is the intended blast
radius of a key rotation.

## Migrations

```bash
cd backend
alembic upgrade head        # deploy step, before the new API/worker start
alembic downgrade -1        # one step back
alembic revision --autogenerate -m "add x"   # a draft to review, never to trust
alembic check               # CI: fails when models and migrations diverge
```

Autogenerate is a drafting tool. Read every generated migration: it will not
notice a column rename (it drops and recreates, losing the data), and it cannot
know whether a new non-null column needs a backfill. `tests/test_migrations.py`
applies the whole chain, rolls it back, reapplies it, and fails on drift.

## Local development

```bash
cd backend
pip install -r requirements.txt
DEV_MODE=1 python -m scripts.seed_demo       # user, mandate, 2y of synthetic history
DEV_MODE=1 uvicorn app.main:app --reload
DEV_MODE=1 python -m app.worker.main         # optional: the scheduler
```

Sign in from the app with `demo@roboadvisor.example` / `demo-password-123`.
Without `FRED_API_KEY` the worker runs its schedule with the FED poller disabled,
which is what you want locally — you can fire the pipeline by hand instead:

```python
trigger = TriggerRouter().evaluate(detect_shift(5.50, 5.25, date.today(), expected_rate_pct=5.50))
await rebalance_all(ctx, trigger)
```

## The schedule

| Job | When | Lock TTL | Why then |
|---|---|---|---|
| `poll_fed` | every 15 min (±30s jitter) | 2 min | FRED publishes daily, but the FOMC statement lands at 18:00 UTC and appears within minutes |
| `ingest_tefas` | 20:30 Europe/Istanbul | 15 min | NAVs are struck after the close and published during the evening |

Every job takes a Redis lock keyed on its name. Two workers must never both see
a policy step and both fire a rebalance, and a rolling deploy overlaps replicas
for exactly long enough for that to happen. The TTL matters more than the lock:
a worker killed mid-job must not block the schedule forever, so a job that
overruns its TTL is a bug to fix, not a case to paper over by raising it.

`coalesce=True` and `max_instances=1` mean a paused machine replays one catch-up
run rather than six, and a slow job never overlaps itself.

Betas are re-estimated **inside** the rebalance job, on the panel as it stands at
decision time — there is deliberately no nightly refit, so a decision can never
rest on a model fitted against data that has since been revised.

## Deploy order

1. `alembic upgrade head` (migrations are backward-compatible with the running version, or the deploy is two releases).
2. Roll the **worker** — it holds locks, so an overlap is safe.
3. Roll the **API** — websocket clients reconnect with backoff and get a fresh snapshot.
4. Roll the **gateway** last, one venue at a time; each restart is a gap in tick
   history, and prices go stale (the UI flags it) until it reconnects.

## What to alert on

| Signal | Why it matters |
|---|---|
| `poll_fed` not succeeding for > 1 hour | a policy move could pass unnoticed |
| tick latency p99 (`gateway.stats.last_latency_ms`) | the difference between live and lying |
| `GatewayStats.dropped` > 0 | the history writer is behind the feed |
| LP solver status ≠ optimal | an infeasible mandate is trading nothing, silently |
| refresh-token family revocations | either token theft or a client refreshing in parallel |
| stale positions during market hours | a dead upstream connection that has not reconnected |
