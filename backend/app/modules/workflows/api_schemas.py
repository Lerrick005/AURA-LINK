"""Pydantic schemas for the Workflow REST API.

These are HTTP-facing. They are distinct from `schemas.py` (which models the
internal graph structure) and `models.py` (which models the database).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.modules.workflows.schemas import WorkflowGraph


class WorkflowCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None


class WorkflowVersionCreate(BaseModel):
    graph: WorkflowGraph


class WorkflowResponse(BaseModel):
    id: str
    name: str
    description: str | None
    status: str
    current_version_id: str | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class WorkflowVersionResponse(BaseModel):
    id: str
    workflow_id: str
    version: int
    created_at: datetime

    model_config = {"from_attributes": True}


class WorkflowRunRequest(BaseModel):
    input_data: dict[str, Any] = Field(
        default_factory=dict,
        description="The trigger input payload for the workflow."
    )
