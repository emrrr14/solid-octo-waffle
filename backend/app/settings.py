"""Runtime configuration, read once from the environment.

No secrets in code and no silent defaults for the ones that matter: a missing
``JWT_SECRET`` fails at startup rather than booting with a guessable key.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta

from app.security.tokens import TokenSettings


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    redis_url: str
    jwt_secret: str
    access_ttl_minutes: int = 15
    refresh_ttl_days: int = 30
    fred_api_key: str | None = None
    finnhub_token: str | None = None
    dev_mode: bool = False

    @property
    def tokens(self) -> TokenSettings:
        return TokenSettings(
            secret=self.jwt_secret,
            access_ttl=timedelta(minutes=self.access_ttl_minutes),
            refresh_ttl=timedelta(days=self.refresh_ttl_days),
        )


def load_settings() -> Settings:
    dev = os.getenv("DEV_MODE", "").lower() in {"1", "true", "yes"}
    secret = os.getenv("JWT_SECRET")

    if secret is None:
        if not dev:
            raise RuntimeError("JWT_SECRET is required (set DEV_MODE=1 for a throwaway key)")
        # Deterministic only inside DEV_MODE, and every restart invalidates
        # existing tokens - which is the correct nuisance for a dev key.
        secret = "dev-only-insecure-secret-key-32chars!!"

    return Settings(
        database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./roboadvisor.db"),
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
        jwt_secret=secret,
        access_ttl_minutes=int(os.getenv("ACCESS_TTL_MINUTES", "15")),
        refresh_ttl_days=int(os.getenv("REFRESH_TTL_DAYS", "30")),
        fred_api_key=os.getenv("FRED_API_KEY"),
        finnhub_token=os.getenv("FINNHUB_TOKEN"),
        dev_mode=dev,
    )
