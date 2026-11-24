"""Response schemas for the /runs read API."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class StepOut(BaseModel):
    seq: int
    type: str
    status: str
    node_id: str | None = None
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    model: str | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: str | None = None
    error: dict[str, Any] | None = None
    started_at: datetime
    finished_at: datetime | None = None


class RunSummary(BaseModel):
    id: str
    workflow_id: str | None = None
    workflow_name: str | None = None
    workflow_version_id: str | None = None
    agent_version_id: str | None = None
    status: str
    terminal_reason: str | None = None
    cost_usd: str
    tokens_in: int
    tokens_out: int
    started_at: datetime
    finished_at: datetime | None = None


class RunDetail(RunSummary):
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    steps: list[StepOut]


class RunList(BaseModel):
    items: list[RunSummary]
    total: int
    limit: int
    offset: int
