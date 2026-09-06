"""FastAPI entry point wiring the pieces together.

Process topology (each is its own deployment):

    market-gateway   1 replica per venue, holds the vendor websockets
    api              N replicas, serves REST + the client websockets  <- this file
    worker           EOD ingestion (TEFAS/BES), macro polling, rebalancing

Everything the request handlers need hangs off ``app.state``: repositories, the
price cache, the FX converter and the token settings.  That is deliberate - it
makes the wiring visible in one place and lets tests substitute any of it.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from app.api import auth as auth_api
from app.api import routes as rest_api
from app.api import ws as ws_api
from app.db.repositories import DecisionRepository, PortfolioRepository
from app.db.session import create_all, create_engine, create_session_factory
from app.market.cache import LastPriceCache
from app.market.valuation import FxConverter
from app.settings import load_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)

    if settings.dev_mode:
        # Production schema changes go through Alembic so they are reviewed like
        # any other code; create_all exists for the dev database only.
        await create_all(engine)

    redis = Redis.from_url(settings.redis_url)

    app.state.settings = settings
    app.state.token_settings = settings.tokens
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.redis = redis
    app.state.cache = LastPriceCache(redis)
    app.state.fx = FxConverter(app.state.cache)
    app.state.portfolios = PortfolioRepository(session_factory)
    app.state.decisions = DecisionRepository(session_factory)

    log.info("api ready (dev_mode=%s, db=%s)", settings.dev_mode, settings.database_url.split("://")[0])
    try:
        yield
    finally:
        await redis.aclose()
        await engine.dispose()


app = FastAPI(title="Robo-Advisor API", lifespan=lifespan)
app.include_router(auth_api.router)
app.include_router(rest_api.router)
app.include_router(ws_api.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
