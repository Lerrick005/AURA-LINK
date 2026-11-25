"""Webhook routes: HMAC-authenticated workflow trigger + secret rotation."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import text

from app.core.db import tenant_session
from app.modules.auth.deps import current_actor
from app.modules.authorization.policy import Actor
from app.modules.webhooks.security import verify_signature, verify_timestamp
from app.modules.workflows.runner import run_workflow_background
from app.modules.workflows.service import (
    WorkflowNotFound,
    start_workflow_run,
)


router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/workflows/{workflow_id}", status_code=status.HTTP_202_ACCEPTED)
async def api_workflow_webhook(
    workflow_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict:
    """Public webhook receiver for a workflow.

    Authentication: HMAC-SHA256 over the raw body, using the workflow's
    stored webhook_secret. Signature and timestamp are mandatory headers.
    No Bearer token required — HMAC is the auth.
    """
    signature_header = request.headers.get("x-auralink-signature-256")
    timestamp_header = request.headers.get("x-auralink-timestamp")
    if not signature_header or not timestamp_header:
        raise HTTPException(status_code=401, detail="missing_signature_headers")
    if not verify_timestamp(timestamp_header):
        raise HTTPException(status_code=401, detail="stale_or_invalid_timestamp")

    body = await request.body()

    # Workflow lookup: workflow_id is globally unique (UUIDv7), so we read
    # cross-org using the owner session. RLS is not violated because we are
    # NOT reading tenant-scoped data here — we only pull id, org_id, secret.
    from app.core.db import get_owner_sessionmaker
    async with get_owner_sessionmaker()() as os_:
        row = (await os_.execute(
            text(
                "SELECT id, org_id, webhook_secret FROM workflows "
                "WHERE id = :id AND status = 'active' AND webhook_secret IS NOT NULL"
            ),
            {"id": workflow_id},
        )).mappings().first()

    if row is None:
        # Same 404 for "not found", "not deployed", and "webhook not enabled"
        # — do not leak which condition failed.
        raise HTTPException(status_code=404, detail="webhook_not_available")

    if not verify_signature(
        secret=row["webhook_secret"],
        body=body,
        header=signature_header,
    ):
        raise HTTPException(status_code=401, detail="invalid_signature")

    org_id = row["org_id"]

    # Payload body is JSON. If malformed, treat as empty input — the workflow
    # trigger node can still run and the agent will receive {}.
    try:
        input_data = await request.json() if body else {}
        if not isinstance(input_data, dict):
            input_data = {"payload": input_data}
    except Exception:
        raise HTTPException(status_code=400, detail="invalid_json_body")

    async with tenant_session(org_id) as s:
        try:
            run_id = await start_workflow_run(
                s, org_id=org_id, workflow_id=workflow_id,
                input_data=input_data,
            )
        except WorkflowNotFound:
            raise HTTPException(status_code=404, detail="workflow_not_deployed")

    background_tasks.add_task(
        run_workflow_background, run_id=run_id, org_id=org_id,
    )
    return {"run_id": run_id, "status": "accepted"}


@router.post("/workflows/{workflow_id}/rotate")
async def api_rotate_webhook_secret(
    workflow_id: str,
    actor: Actor = Depends(current_actor),
) -> dict:
    """Generate a new webhook secret. Returns the raw secret once.

    The caller must save it immediately — it is not retrievable via the API
    again. Rotating invalidates the previous secret on the next webhook call.
    """
    new_secret = secrets.token_urlsafe(32)
    async with tenant_session(actor.org_id) as s:
        result = await s.execute(
            text(
                "UPDATE workflows SET webhook_secret = :sec "
                "WHERE id = :id AND org_id = :org RETURNING id"
            ),
            {"sec": new_secret, "id": workflow_id, "org": actor.org_id},
        )
        if result.first() is None:
            raise HTTPException(status_code=404, detail="workflow_not_found")
    return {
        "workflow_id": workflow_id,
        "webhook_secret": new_secret,
        "warning": "Store this secret now. It will not be shown again.",
    }
