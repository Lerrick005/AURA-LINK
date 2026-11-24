"""HTTP routes for Workflow management and execution."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.core.db import tenant_session
from app.modules.auth.deps import current_actor
from app.modules.authorization.policy import Actor
from app.modules.workflows.api_schemas import (
    WorkflowCreate,
    WorkflowResponse,
    WorkflowRunRequest,
    WorkflowVersionCreate,
    WorkflowVersionResponse,
)
from app.modules.workflows.engine_factory import build_workflow_engine
from app.modules.workflows.service import (
    InvalidWorkflowGraph,
    WorkflowNotFound,
    create_version,
    create_workflow,
    deploy_version,
    get_workflow,
    list_workflows,
    start_workflow_run,
)


router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.post("", response_model=WorkflowResponse, status_code=status.HTTP_201_CREATED)
async def api_create_workflow(
    body: WorkflowCreate,
    actor: Actor = Depends(current_actor),
) -> WorkflowResponse:
    async with tenant_session(actor.org_id) as s:
        try:
            wf = await create_workflow(
                s, org_id=actor.org_id,
                name=body.name, description=body.description,
                created_by=actor.user_id,
            )
        except Exception as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        await s.refresh(wf)
        return WorkflowResponse.model_validate(wf, from_attributes=True)


@router.get("", response_model=list[WorkflowResponse])
async def api_list_workflows(
    actor: Actor = Depends(current_actor),
) -> list[WorkflowResponse]:
    async with tenant_session(actor.org_id) as s:
        rows = await list_workflows(s, org_id=actor.org_id)
        return [WorkflowResponse.model_validate(r, from_attributes=True) for r in rows]


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def api_get_workflow(
    workflow_id: str,
    actor: Actor = Depends(current_actor),
) -> WorkflowResponse:
    async with tenant_session(actor.org_id) as s:
        try:
            wf = await get_workflow(s, org_id=actor.org_id, workflow_id=workflow_id)
        except WorkflowNotFound:
            raise HTTPException(status_code=404, detail="workflow_not_found")
        return WorkflowResponse.model_validate(wf, from_attributes=True)


@router.post(
    "/{workflow_id}/versions",
    response_model=WorkflowVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def api_create_version(
    workflow_id: str,
    body: WorkflowVersionCreate,
    actor: Actor = Depends(current_actor),
) -> WorkflowVersionResponse:
    async with tenant_session(actor.org_id) as s:
        try:
            ver = await create_version(
                s, org_id=actor.org_id, workflow_id=workflow_id,
                graph=body.graph, created_by=actor.user_id,
            )
        except WorkflowNotFound:
            raise HTTPException(status_code=404, detail="workflow_not_found")
        except InvalidWorkflowGraph as exc:
            raise HTTPException(
                status_code=422,
                detail={"message": "invalid_workflow_graph", "errors": exc.errors},
            )
        return WorkflowVersionResponse.model_validate(ver, from_attributes=True)


@router.post("/{workflow_id}/deploy", response_model=WorkflowResponse)
async def api_deploy_workflow(
    workflow_id: str,
    version_id: str,
    actor: Actor = Depends(current_actor),
) -> WorkflowResponse:
    async with tenant_session(actor.org_id) as s:
        try:
            wf = await deploy_version(
                s, org_id=actor.org_id, workflow_id=workflow_id,
                workflow_version_id=version_id,
            )
        except WorkflowNotFound:
            raise HTTPException(status_code=404, detail="workflow_or_version_not_found")
        await s.refresh(wf)
        return WorkflowResponse.model_validate(wf, from_attributes=True)


@router.post("/{workflow_id}/runs", status_code=status.HTTP_202_ACCEPTED)
async def api_trigger_workflow(
    workflow_id: str,
    body: WorkflowRunRequest,
    background_tasks: BackgroundTasks,
    actor: Actor = Depends(current_actor),
) -> dict:
    """Create the run row, return its id, execute the workflow in the background.

    For MVP this uses FastAPI's BackgroundTasks (in-process). In production
    this should enqueue to a real worker (Celery/RQ) so API process restarts
    do not orphan in-flight runs. That decision belongs with ADR-001's revisit
    trigger for worker topology.
    """
    org_id = actor.org_id
    async with tenant_session(org_id) as s:
        try:
            run_id = await start_workflow_run(
                s, org_id=org_id, workflow_id=workflow_id,
                input_data=body.input_data,
            )
        except WorkflowNotFound:
            raise HTTPException(status_code=404, detail="workflow_not_found_or_not_deployed")

    async def _run_workflow() -> None:
        import logging
        log = logging.getLogger(__name__)
        try:
            engine = build_workflow_engine()
            await engine.run_once(run_id=run_id, org_id=org_id)
        except Exception:
            log.exception("workflow_run_failed", extra={"run_id": run_id, "org_id": org_id})

    background_tasks.add_task(_run_workflow)
    return {"run_id": run_id, "status": "accepted"}
