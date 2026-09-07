"""Alembic environment.

Async engine, because the app's is async and running migrations through a second
sync driver means a second driver to install and a second DSN dialect to get
right.  ``render_as_batch`` is on so SQLite (dev, CI) can perform ALTERs that it
does not natively support - without it, the first column change breaks every
developer's database while working fine in Postgres.
"""
from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy import pool

from app.db.models import Base

DEFAULT_URL = "sqlite+aiosqlite:///./roboadvisor.db"

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Precedence: a URL the caller set programmatically (app.db.migrate), then
# DATABASE_URL, then the dev default.  Migrations deliberately do NOT go through
# app.settings - running them must not require a JWT secret or any other runtime
# configuration the schema has nothing to do with.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option("sqlalchemy.url", os.getenv("DATABASE_URL", DEFAULT_URL))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
