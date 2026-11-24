"""Workflow service: create, version, deploy, get, list.

Every function takes an AsyncSession bound to a tenant via tenant_session().
Callers control the transaction boundary. The service never commits.

Version numbers use MAX(version)+1 inside the caller's transaction, with
the unique constraint (workflow_id, version) as backstop. Same pattern as
agents.service.create_version.

Graph validation runs before persist. Agent-id and tool-key visibility
checks are done here (they need DB access, unlike graph.validate_graph
which is pure).
"""
from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import uuid7str
from app.modules.workflows.graph import validate_graph
from app.modules.workflows.models import Workflow, WorkflowVersion
from app.modules.workflows.schemas import WorkflowGraph


class WorkflowError(Exception):
    pass


class WorkflowNotFound(WorkflowError):
    pass


class WorkflowVersionNotFound(WorkflowError):
    pass


class DuplicateWorkflowName(WorkflowError):
    pass


class InvalidWorkflowGraph(WorkflowError):
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("invalid workflow graph: " + "; ".join(errors))


class WorkflowNotDeployed(WorkflowError):
    pass


async def create_workflow(
    session: AsyncSession, *, org_id: str, name: str,
    description: str | None = None, created_by: str | None = None,
) -> Workflow:
    """Create a workflow in draft state with no version. Cannot run yet."""
    wf = Workflow(
        id=uuid7str(), org_id=org_id, name=name,
        description=description, status="draft",
        created_by=created_by,
    )
    session.add(wf)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateWorkflowName(
            f"workflow name already exists: {name!r}"
        ) from exc
    return wf


async def _check_graph_refs(
    session: AsyncSession, *, org_id: str, graph: WorkflowGraph,
) -> list[str]:
    """DB-backed checks: agent_ids and tool_keys must be visible to this org."""
    errors: list[str] = []

    agent_ids = [
        n.config.agent_id for n in graph.nodes
        if n.type.value == "agent" and n.config and n.config.agent_id
    ]
    if agent_ids:
        rows = (await session.execute(
            text(
                "SELECT id FROM agents WHERE org_id = :o AND id = ANY(:ids)"
            ),
            {"o": org_id, "ids": agent_ids},
        )).scalars().all()
        found = set(rows)
        for aid in agent_ids:
            if aid not in found:
                errors.append(f"agent_id not found or not visible: {aid!r}")

    tool_keys = [
        n.config.tool_key for n in graph.nodes
        if n.type.value == "tool" and n.config and n.config.tool_key
    ]
    if tool_keys:
        rows = (await session.execute(
            text(
                "SELECT key FROM tools "
                "WHERE (org_id IS NULL OR org_id = :o) AND key = ANY(:keys)"
            ),
            {"o": org_id, "keys": tool_keys},
        )).scalars().all()
        found = set(rows)
        for key in tool_keys:
            if key not in found:
                errors.append(f"tool_key not found or not visible: {key!r}")

    return errors


async def create_version(
    session: AsyncSession, *, org_id: str, workflow_id: str,
    graph: WorkflowGraph, created_by: str | None = None,
) -> WorkflowVersion:
    """Create a new immutable version. Validates graph shape AND DB refs."""
    wf = await session.get(Workflow, workflow_id)
    if wf is None or wf.org_id != org_id:
        raise WorkflowNotFound(workflow_id)

    errors = validate_graph(graph)
    if not errors:
        errors = await _check_graph_refs(session, org_id=org_id, graph=graph)
    if errors:
        raise InvalidWorkflowGraph(errors)

    # MAX(version)+1 within caller's transaction
    result = await session.execute(
        text(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM workflow_versions "
            "WHERE workflow_id = :wid"
        ),
        {"wid": workflow_id},
    )
    next_version = int(result.scalar_one())

    ver = WorkflowVersion(
        id=uuid7str(),
        workflow_id=workflow_id,
        org_id=org_id,
        version=next_version,
        graph=graph.model_dump(mode="json"),
        created_by=created_by,
    )
    session.add(ver)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise WorkflowError(
            "version number conflict; retry create_version"
        ) from exc
    return ver


async def deploy_version(
    session: AsyncSession, *, org_id: str, workflow_id: str,
    workflow_version_id: str,
) -> Workflow:
    """Point workflows.current_version_id at a version and set status=active."""
    wf = await session.get(Workflow, workflow_id)
    if wf is None or wf.org_id != org_id:
        raise WorkflowNotFound(workflow_id)

    ver = await session.get(WorkflowVersion, workflow_version_id)
    if ver is None or ver.org_id != org_id or ver.workflow_id != workflow_id:
        raise WorkflowVersionNotFound(workflow_version_id)

    wf.current_version_id = workflow_version_id
    wf.status = "active"
    await session.flush()
    return wf


async def get_workflow(
    session: AsyncSession, *, org_id: str, workflow_id: str,
) -> Workflow:
    wf = await session.get(Workflow, workflow_id)
    if wf is None or wf.org_id != org_id:
        raise WorkflowNotFound(workflow_id)
    return wf


async def list_workflows(
    session: AsyncSession, *, org_id: str,
) -> list[Workflow]:
    result = await session.execute(
        select(Workflow)
        .where(Workflow.org_id == org_id)
        .order_by(Workflow.created_at.desc())
    )
    return list(result.scalars().all())


async def resolve_runtime_graph(
    session: AsyncSession, *, org_id: str, workflow_id: str,
) -> tuple[Workflow, WorkflowVersion, WorkflowGraph]:
    """The single entry point the runtime uses to load a workflow.

    Returns the workflow row, its current version, and the parsed graph.
    Raises if the workflow is not deployed.
    """
    wf = await session.get(Workflow, workflow_id)
    if wf is None or wf.org_id != org_id:
        raise WorkflowNotFound(workflow_id)
    if wf.current_version_id is None or wf.status != "active":
        raise WorkflowNotDeployed(workflow_id)

    ver = await session.get(WorkflowVersion, wf.current_version_id)
    if ver is None or ver.org_id != org_id:
        raise WorkflowVersionNotFound(wf.current_version_id)

    graph = WorkflowGraph.model_validate(ver.graph)
    return wf, ver, graph


__all__ = [
    "WorkflowError", "WorkflowNotFound", "WorkflowVersionNotFound",
    "DuplicateWorkflowName", "InvalidWorkflowGraph", "WorkflowNotDeployed",
    "create_workflow", "create_version", "deploy_version",
    "get_workflow", "list_workflows", "resolve_runtime_graph",
    "start_workflow_run",
]

async def start_workflow_run(
    session: AsyncSession,
    *,
    org_id: str,
    workflow_id: str,
    input_data: dict,
) -> str:
    """Create a pending agent_runs row for a workflow execution.

    Workflow runs have NULL agent_version_id (they are not agent executions).
    The workflow_version_id is resolved from the currently deployed version,
    so deploying a new version while an old run is in flight does not change
    what that run executes.
    """
    import json as _json
    from sqlalchemy import text as _text
    from app.core.ids import uuid7str as _uuid7str

    _, ver, _ = await resolve_runtime_graph(
        session, org_id=org_id, workflow_id=workflow_id
    )

    run_id = _uuid7str()
    await session.execute(
        _text(
            "INSERT INTO agent_runs "
            "(id, org_id, agent_version_id, workflow_version_id, status, input) "
            "VALUES (:id, :o, NULL, :wv, 'pending', CAST(:inp AS jsonb))"
        ),
        {
            "id": run_id,
            "o": org_id,
            "wv": ver.id,
            "inp": _json.dumps(input_data),
        },
    )
    await session.flush()
    return run_id
