"""Shared FastAPI dependencies.

tenant_context() is the ONLY sanctioned way for a route to obtain an
AsyncSession bound to a tenant. It depends on the authenticated user and
org membership (Milestone 2 wires auth); today it is present but unused.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_sessionmaker


async def db_session() -> AsyncIterator[AsyncSession]:
    """Unscoped session for endpoints that do NOT touch tenant data (e.g., health).

    Any endpoint that reads or writes tenant-owned tables MUST use
    tenant_context() from app.core.db instead of this.
    """
    sm = get_sessionmaker()
    async with sm() as session:
        yield session


async def request_id_header(
    x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> str | None:
    """Pass through a caller-supplied request ID; validated in middleware."""
    return x_request_id
