"""Health and meta endpoints. No tenant context required."""

from __future__ import annotations

from fastapi import APIRouter, Response

from app.core.config import get_settings
from app.core.db import healthcheck

router = APIRouter()


@router.get("/healthz", tags=["meta"])
async def healthz() -> dict[str, str]:
    """Liveness + DB readiness. Returns overall=ok or degraded."""
    db = await healthcheck()
    overall = "ok" if db.get("db") == "ok" else "degraded"
    return {"status": overall, "db": db.get("db", "unknown")}


@router.get("/__meta", tags=["meta"])
async def meta() -> dict[str, str]:
    s = get_settings()
    return {
        "name": s.APP_NAME,
        "version": s.APP_VERSION,
        "env": s.APP_ENV,
    }


@router.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Prometheus exposition stub. Real metrics land in Milestone 7 (ADR-0013)."""
    body = (
        "# HELP auralink_up 1 if the process is up",
        "# TYPE auralink_up gauge",
        "auralink_up 1",
    )
    return Response(content="\n".join(body) + "\n", media_type="text/plain; version=0.0.4")
