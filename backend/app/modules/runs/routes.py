"""HTTP routes for reading runs and their execution steps."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.db import tenant_session
from app.modules.auth.deps import current_actor
from app.modules.authorization.policy import Actor
from app.modules.runs.schemas import RunDetail, RunList
from app.modules.runs.service import RunNotFound, get_run_detail, list_runs


router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("", response_model=RunList)
async def api_list_runs(
    workflow_id: str | None = Query(None, description="Filter by workflow id"),
    status: str | None = Query(None, description="Filter by status (e.g. completed)"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    actor: Actor = Depends(current_actor),
) -> RunList:
    async with tenant_session(actor.org_id) as s:
        return await list_runs(
            s, org_id=actor.org_id,
            workflow_id=workflow_id, status=status,
            limit=limit, offset=offset,
        )


@router.get("/{run_id}", response_model=RunDetail)
async def api_get_run(
    run_id: str,
    actor: Actor = Depends(current_actor),
) -> RunDetail:
    async with tenant_session(actor.org_id) as s:
        try:
            return await get_run_detail(s, org_id=actor.org_id, run_id=run_id)
        except RunNotFound:
            raise HTTPException(status_code=404, detail="run_not_found")
