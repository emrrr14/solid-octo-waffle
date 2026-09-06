"""FastAPI entry point wiring the pieces together.

Process topology (each of these is its own deployment):

    market-gateway   1 replica per venue, holds the vendor websockets
    api              N replicas, serves REST + the client websockets
    worker           EOD ingestion (TEFAS/BES), macro polling, rebalancing
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from redis.asyncio import Redis

from app.api import routes as rest_api
from app.api import ws as ws_api
from app.market.cache import LastPriceCache
from app.market.valuation import FxConverter

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    redis = Redis.from_url("redis://localhost:6379/0")
    app.state.redis = redis
    app.state.cache = LastPriceCache(redis)
    app.state.fx = FxConverter(app.state.cache)
    app.state.portfolios = PortfolioRepository()      # swap for the Postgres-backed one
    try:
        yield
    finally:
        await redis.aclose()


class PortfolioRepository:
    """Placeholder repository - replace with the SQLAlchemy implementation."""

    async def get(self, portfolio_id: str):
        return None


app = FastAPI(title="Robo-Advisor API", lifespan=lifespan)
app.include_router(ws_api.router)
app.include_router(rest_api.router)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
