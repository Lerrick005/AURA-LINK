"""Alembic environment for Auralink.

Loads the OWNER-role URL from Settings.ALEMBIC_DATABASE_URL, not the runtime
URL. Migrations create tables and RLS policies; they must run as the owner,
not as the RLS-enforced runtime role. See ADR-0017.

Supports both online (async engine) and offline (offline SQL string) modes.
Offline mode does NOT call get_settings() so it works without full env.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from app.modules.agents.models import Base

config = context.config

# Interpret the config file for Python logging if present. Do not overwrite
# handlers if the app already configured them.
if config.config_file_name is not None and not os.environ.get("AURALINK_APP_LOGGING"):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _owner_url() -> str:
    """Owner-role URL from Settings. Imported lazily so offline mode works."""
    from app.core.config import get_settings
    url = get_settings().ALEMBIC_DATABASE_URL
    if not url.startswith("postgresql+asyncpg://"):
        raise RuntimeError(
            "ALEMBIC_DATABASE_URL must use postgresql+asyncpg:// (got: " + url.split(":")[0] + ")"
        )
    return url


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting."""
    url = config.get_main_option("sqlalchemy.url") or os.environ.get("ALEMBIC_DATABASE_URL")
    if not url:
        # In offline mode we require the URL from the config or env, but we do
        # NOT call get_settings() so that partial environments still work.
        raise RuntimeError("offline mode requires sqlalchemy.url or ALEMBIC_DATABASE_URL")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        # Include schema-qualified names as-is.
        include_schemas=False,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    url = _owner_url()
    config.set_main_option("sqlalchemy.url", url)
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section) or {},
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
