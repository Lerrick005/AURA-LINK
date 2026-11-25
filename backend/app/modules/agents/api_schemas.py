"""Response/request schemas for the /agents HTTP API."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.modules.agents.schemas import AgentConfig


class AgentCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class AgentResponse(BaseModel):
    id: str
    name: str
    status: str
    current_version_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class AgentVersionCreate(BaseModel):
    config: AgentConfig


class AgentVersionResponse(BaseModel):
    id: str
    agent_id: str
    version: int
    created_at: datetime

    model_config = {"from_attributes": True}


class AgentRunRequest(BaseModel):
    input_data: dict[str, Any] = Field(
        default_factory=dict,
        description="The task input payload for the agent run.",
    )
