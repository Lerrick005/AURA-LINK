"""Database engine, session factory, and RLS enforcement.

Two engines:
- get_engine()/get_sessionmaker()             -> runtime role (auralink_app), RLS-enforced.
- get_owner_engine()/get_owner_sessionmaker() -> owner role (auralink), for
  migrations and admin_session() only.

See ADR-0002.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None
_owner_engine: AsyncEngine | None = None
_owner_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        s = get_settings()
        _engine = create_async_engine(
            s.DATABASE_URL,
            pool_size=s.DATABASE_POOL_SIZE,
            max_overflow=s.DATABASE_POOL_MAX_OVERFLOW,
            pool_pre_ping=True,
            pool_recycle=1800,
            future=True,
            connect_args={
                "server_settings": {
                    "application_name": "auralink_app",
                    "statement_timeout": str(s.DATABASE_STATEMENT_TIMEOUT_MS),
                },
            },
        )
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            bind=get_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _sessionmaker


def get_owner_engine() -> AsyncEngine:
    global _owner_engine
    if _owner_engine is None:
        s = get_settings()
        _owner_engine = create_async_engine(
            s.ALEMBIC_DATABASE_URL,
            pool_size=5,
            max_overflow=2,
            pool_pre_ping=True,
            pool_recycle=1800,
            future=True,
            connect_args={
                "server_settings": {
                    "application_name": "auralink_owner",
                    "statement_timeout": str(s.DATABASE_STATEMENT_TIMEOUT_MS),
                },
            },
        )
    return _owner_engine


def get_owner_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _owner_sessionmaker
    if _owner_sessionmaker is None:
        _owner_sessionmaker = async_sessionmaker(
            bind=get_owner_engine(),
            expire_on_commit=False,
            autoflush=False,
        )
    return _owner_sessionmaker


async def dispose_engine() -> None:
    """Call from application shutdown."""
    global _engine, _sessionmaker, _owner_engine, _owner_sessionmaker
    if _engine is not None:
        await _engine.dispose()
    if _owner_engine is not None:
        await _owner_engine.dispose()
    _engine = None
    _sessionmaker = None
    _owner_engine = None
    _owner_sessionmaker = None


@contextlib.asynccontextmanager
async def tenant_session(org_id: uuid.UUID | str) -> AsyncIterator[AsyncSession]:
    """Yield a session scoped to one org via set_config.'app.org_id'.'

    Transaction-local setting, so pooled connections cannot leak tenant context.
    Fails closed: no org_id -> raise.
    """
    if not org_id:
        raise ValueError("tenant_session requires an org_id")
    org_str = str(org_id)
    sm = get_sessionmaker()
    async with sm() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.org_id', :oid, true)"),
                {"oid": org_str},
            )
            yield session


@contextlib.asynccontextmanager
async def admin_session() -> AsyncIterator[AsyncSession]:
    """Owner-role session for migrations/maintenance. RLS bypassed; audit required."""
    sm = get_owner_sessionmaker()
    async with sm() as session:
        async with session.begin():
            yield session


async def healthcheck() -> dict[str, Any]:
    """Lightweight DB check for /healthz. Never sets tenant context."""
    try:
        sm = get_sessionmaker()
        async with sm() as session:
            await session.execute(text("SELECT 1"))
        return {"db": "ok"}
    except Exception as exc:
        log.error("db_healthcheck_failed", extra={"error": str(exc)})
        return {"db": "error"}
