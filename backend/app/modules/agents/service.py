"""Agent lifecycle service.

Every function takes an AsyncSession bound to a tenant via tenant_session().
Callers control the transaction boundary. The service never commits.

Immutability rule:
    agent_versions are created, never updated. Publishing a change is:
    create_version(N+1) -> deploy_version(N+1). agents.current_version_id
    is the only pointer that flips.

Concurrency:
    Version numbers are assigned inside the caller's transaction via
    MAX(version)+1. The unique constraint uq_agent_versions_agent_version
    is the backstop if two writers race; the caller retries with a fresh
    read. We do not use SERIALIZABLE for this -- the constraint is cheaper.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ids import uuid7str
from app.core.logging import get_logger
from app.modules.agents.models import (
    Agent,
    AgentToolGrant,
    AgentVersion,
    Tool,
)
from app.modules.agents.schemas import (
    AgentConfig,
    ResolvedAgentConfig,
    ResolvedTool,
)

log = get_logger(__name__)


class AgentServiceError(Exception):
    """Base for all service errors. Callers map to HTTP or terminal outcomes."""


class AgentNotFound(AgentServiceError):
    pass


class AgentNoVersion(AgentServiceError):
    pass


class AgentNotDeployed(AgentServiceError):
    pass


class ToolNotFound(AgentServiceError):
    pass


class DuplicateAgentName(AgentServiceError):
    pass


async def create_agent(
    session: AsyncSession, *, org_id: str, name: str
) -> Agent:
    """Create an agent in draft state with no version. Cannot run yet."""
    agent = Agent(id=uuid7str(), org_id=org_id, name=name, status="draft")
    session.add(agent)
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise DuplicateAgentName(f"agent name already exists: {name!r}") from exc
    return agent


async def create_version(
    session: AsyncSession,
    *,
    org_id: str,
    agent_id: str,
    config: AgentConfig,
    created_by: str | None = None,
) -> AgentVersion:
    """Create a new immutable version for an agent."""
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.org_id != org_id:
        raise AgentNotFound(agent_id)

    # MAX(version)+1 within the caller's transaction.
    result = await session.execute(
        text("SELECT COALESCE(MAX(version), 0) + 1 FROM agent_versions WHERE agent_id = :aid"),
        {"aid": agent_id},
    )
    next_version = int(result.scalar_one())

    version = AgentVersion(
        id=uuid7str(),
        agent_id=agent_id,
        org_id=org_id,
        version=next_version,
        config=config.model_dump(mode="json"),
        created_by=created_by,
    )
    session.add(version)
    try:
        await session.flush()
    except IntegrityError as exc:
        # Unique constraint on (agent_id, version). Caller can retry.
        raise AgentServiceError(
            "version number conflict; retry create_version"
        ) from exc
    return version


async def grant_tools(
    session: AsyncSession,
    *,
    org_id: str,
    agent_version_id: str,
    grants: Sequence[tuple[str, dict[str, Any]]],
) -> None:
    """Replace the tool allowlist for a version.

    Deletes existing grants and inserts the new set atomically within the
    caller's transaction. Every tool_id must exist and be visible to this
    org (builtin NULL-org, or owned by org).
    """
    version = await session.get(AgentVersion, agent_version_id)
    if version is None or version.org_id != org_id:
        raise AgentNoVersion(agent_version_id)

    tool_ids = [tid for tid, _ in grants]
    if tool_ids:
        result = await session.execute(
            select(Tool.id).where(Tool.id.in_(tool_ids)).where(
                (Tool.org_id.is_(None)) | (Tool.org_id == org_id)
            )
        )
        visible = set(result.scalars().all())
        missing = set(tool_ids) - visible
        if missing:
            raise ToolNotFound(f"tools not visible to org: {sorted(missing)}")

    await session.execute(
        text("DELETE FROM agent_tool_grants WHERE agent_version_id = :v"),
        {"v": agent_version_id},
    )
    for tool_id, cfg in grants:
        session.add(
            AgentToolGrant(
                id=uuid7str(),
                agent_version_id=agent_version_id,
                tool_id=tool_id,
                org_id=org_id,
                config=cfg,
            )
        )
    await session.flush()


async def deploy_version(
    session: AsyncSession, *, org_id: str, agent_id: str, agent_version_id: str
) -> Agent:
    """Point agents.current_version_id at a version and set status=active.

    Refuses if the version has no tool grants -- an agent without tools is a
    misconfiguration, not a design. If an agent legitimately needs no tools,
    that is a config flag we add later, not a silent exception.
    """
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.org_id != org_id:
        raise AgentNotFound(agent_id)

    version = await session.get(AgentVersion, agent_version_id)
    if version is None or version.org_id != org_id or version.agent_id != agent_id:
        raise AgentNoVersion(agent_version_id)

    grant_count = (
        await session.execute(
            text("SELECT COUNT(*) FROM agent_tool_grants WHERE agent_version_id = :v"),
            {"v": agent_version_id},
        )
    ).scalar_one()
    if int(grant_count) == 0:
        raise AgentServiceError(
            "cannot deploy a version with no tool grants"
        )

    agent.current_version_id = agent_version_id
    agent.status = "active"
    await session.flush()
    return agent


async def resolve_runtime_config(
    session: AsyncSession, *, org_id: str, agent_id: str
) -> ResolvedAgentConfig:
    """The single entry point the runtime uses to load an agent.

    Returns a frozen snapshot. Joins version config with resolved tool rows.
    Runtime must not query agent tables directly.
    """
    agent = await session.get(Agent, agent_id)
    if agent is None or agent.org_id != org_id:
        raise AgentNotFound(agent_id)
    if agent.current_version_id is None or agent.status != "active":
        raise AgentNotDeployed(agent_id)

    version = await session.get(AgentVersion, agent.current_version_id)
    if version is None or version.org_id != org_id:
        raise AgentNoVersion(agent.current_version_id)

    # Validate the stored config against the current schema. A version written
    # under an older schema must fail loudly rather than produce a run with
    # missing fields.
    config = AgentConfig.model_validate(version.config)

    result = await session.execute(
        text(
            "SELECT g.tool_id, g.config AS grant_config, "
            "       t.key, t.name, t.description, t.input_schema, t.output_schema, "
            "       t.risk_class, t.approval_policy, t.timeout_ms, t.idempotency_mode "
            "FROM agent_tool_grants g "
            "JOIN tools t ON t.id = g.tool_id "
            "WHERE g.agent_version_id = :v "
            "ORDER BY t.key"
        ),
        {"v": version.id},
    )
    tools: list[ResolvedTool] = []
    for row in result.mappings():
        tools.append(
            ResolvedTool(
                id=row["tool_id"],
                key=row["key"],
                name=row["name"],
                description=row["description"],
                input_schema=row["input_schema"],
                output_schema=row["output_schema"],
                risk_class=row["risk_class"],
                approval_policy=row["approval_policy"],
                timeout_ms=row["timeout_ms"],
                idempotency_mode=row["idempotency_mode"],
                grant_config=row["grant_config"] or {},
            )
        )

    return ResolvedAgentConfig(
        agent_id=agent.id,
        agent_name=agent.name,
        version_id=version.id,
        version=version.version,
        org_id=org_id,
        config=config,
        tools=tuple(tools),
        knowledge_scope_all_org_documents=True,
    )


async def list_agents(session: AsyncSession, *, org_id: str) -> list[Agent]:
    result = await session.execute(
        select(Agent).where(Agent.org_id == org_id).order_by(Agent.created_at.desc())
    )
    return list(result.scalars().all())
