"""Applying migrations from Python.

Dev and tests want "make the schema current" as one call; production runs
``alembic upgrade head`` as a deploy step so a failed migration stops the
rollout instead of a process quietly booting against the wrong schema.  Both go
through the same migration files - `create_all` is used only by the unit-test
fixtures, where the schema lives for milliseconds.
"""
from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config

log = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def alembic_config(database_url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def upgrade_to_head(database_url: str) -> None:
    log.info("applying migrations to %s", database_url.split("://")[0])
    command.upgrade(alembic_config(database_url), "head")
