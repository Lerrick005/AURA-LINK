"""HTTP routes for Agent management and execution."""
from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from app.core.db import tenant_session
from app.modules.agents.api_schemas import (
    AgentCreate,
    AgentResponse,
    AgentRunRequest,
    AgentVersionCreate,
    AgentVersionResponse,
)
from app.modules.agents.models import Agent
from app.modules.agents.runner import run_agent_background
from app.modules.agents.service import (
    AgentNotFound,
    AgentNoVersion,
    AgentNotDeployed,
    DuplicateAgentName,
    ToolNotFound,
    create_agent,
    create_version,
    deploy_version,
    start_run,
)
from app.modules.auth.deps import current_actor
from app.modules.authorization.policy import Actor
from sqlalchemy import select


router = APIRouter(prefix="/agents", tags=["agents"])


@router.post("", response_model=AgentResponse, status_code=status.HTTP_201_CREATED)
async def api_create_agent(
    body: AgentCreate,
    actor: Actor = Depends(current_actor),
) -> AgentResponse:
    async with tenant_session(actor.org_id) as s:
        try:
            agent = await create_agent(s, org_id=actor.org_id, name=body.name)
        except DuplicateAgentName as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        return AgentResponse.model_validate(agent, from_attributes=True)


@router.get("", response_model=list[AgentResponse])
async def api_list_agents(
    actor: Actor = Depends(current_actor),
) -> list[AgentResponse]:
    async with tenant_session(actor.org_id) as s:
        rows = (await s.execute(
            select(Agent).where(Agent.org_id == actor.org_id).order_by(Agent.created_at.desc())
        )).scalars().all()
        return [AgentResponse.model_validate(r, from_attributes=True) for r in rows]


@router.get("/{agent_id}", response_model=AgentResponse)
async def api_get_agent(
    agent_id: str,
    actor: Actor = Depends(current_actor),
) -> AgentResponse:
    async with tenant_session(actor.org_id) as s:
        agent = await s.get(Agent, agent_id)
        if agent is None or agent.org_id != actor.org_id:
            raise HTTPException(status_code=404, detail="agent_not_found")
        return AgentResponse.model_validate(agent, from_attributes=True)


@router.post(
    "/{agent_id}/versions",
    response_model=AgentVersionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def api_create_agent_version(
    agent_id: str,
    body: AgentVersionCreate,
    actor: Actor = Depends(current_actor),
) -> AgentVersionResponse:
    async with tenant_session(actor.org_id) as s:
        try:
            ver = await create_version(
                s, org_id=actor.org_id, agent_id=agent_id,
                config=body.config, created_by=actor.user_id,
            )
        except AgentNotFound:
            raise HTTPException(status_code=404, detail="agent_not_found")
        return AgentVersionResponse.model_validate(ver, from_attributes=True)


@router.post("/{agent_id}/deploy", response_model=AgentResponse)
async def api_deploy_agent(
    agent_id: str,
    version_id: str,
    actor: Actor = Depends(current_actor),
) -> AgentResponse:
    async with tenant_session(actor.org_id) as s:
        try:
            agent = await deploy_version(
                s, org_id=actor.org_id, agent_id=agent_id,
                agent_version_id=version_id,
            )
        except AgentNotFound:
            raise HTTPException(status_code=404, detail="agent_or_version_not_found")
        except ToolNotFound as exc:
            raise HTTPException(status_code=422, detail=f"tool_not_found: {exc}")
        except (AgentNoVersion, AgentNotDeployed) as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        await s.refresh(agent)
        return AgentResponse.model_validate(agent, from_attributes=True)


@router.post("/{agent_id}/runs", status_code=status.HTTP_202_ACCEPTED)
async def api_trigger_agent(
    agent_id: str,
    body: AgentRunRequest,
    background_tasks: BackgroundTasks,
    actor: Actor = Depends(current_actor),
) -> dict:
    org_id = actor.org_id
    async with tenant_session(org_id) as s:
        try:
            run_id = await start_run(
                s, org_id=org_id, agent_id=agent_id,
                input_data=body.input_data,
            )
        except AgentNotFound:
            raise HTTPException(status_code=404, detail="agent_not_found")
        except AgentNotDeployed:
            raise HTTPException(status_code=409, detail="agent_not_deployed")

    background_tasks.add_task(
        run_agent_background, run_id=run_id, org_id=org_id,
    )
    return {"run_id": run_id, "status": "accepted"}
