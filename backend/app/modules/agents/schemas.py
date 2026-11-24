"""Pydantic contracts for the agent engine.

Two shapes:
- ``AgentConfig`` is the JSONB stored in agent_versions.config. Validated on
  every write, frozen so a runtime run sees a stable snapshot.
- ``ResolvedAgentConfig`` is what the runtime gets when it asks the service for
  an agent. It joins the version config with resolved tool rows so the runtime
  never has to join tables itself.

Both are frozen. If the runtime needs to change something, that is a new version.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


RiskClass = Literal[
    "read_only",
    "reversible_write",
    "irreversible_write",
    "financial",
    "external_communication",
]

ApprovalPolicy = Literal["auto", "always", "above_threshold"]

IdempotencyMode = Literal["none", "keyed", "tool_managed"]


class AgentLimits(BaseModel):
    """Hard caps enforced OUTSIDE the model. A run exceeds one -> terminates."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_steps: int = Field(default=10, ge=1, le=100)
    max_tool_calls: int = Field(default=20, ge=0, le=200)
    max_seconds: int = Field(default=120, ge=1, le=3600)
    max_cost_usd: Decimal = Field(default=Decimal("0.50"), gt=Decimal("0"))


class AgentMemoryConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = False
    # Revisit when contact memory lands; this stub prevents a schema migration
    # for a flag we will certainly need.


class EscalationRules(BaseModel):
    """When the agent must stop and hand off. Checked by the runtime, not the model."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    on_low_confidence_below: float | None = Field(default=None, ge=0.0, le=1.0)
    on_tool_failure: bool = False
    on_repeated_action: bool = True
    on_cost_above_usd: Decimal | None = Field(default=None, ge=Decimal("0"))


class AgentConfig(BaseModel):
    """The immutable JSONB content of an agent_versions row.

    Changing any field requires creating a new version. Update-in-place is
    forbidden at the service layer.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    instructions: str = Field(min_length=1, max_length=20000)
    model: str = Field(min_length=1, max_length=100)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_output_tokens: int = Field(default=1024, ge=1, le=32000)
    limits: AgentLimits = Field(default_factory=AgentLimits)
    memory: AgentMemoryConfig = Field(default_factory=AgentMemoryConfig)
    escalation: EscalationRules = Field(default_factory=EscalationRules)
    expected_terminal_tool: str | None = None
    require_verified_terminal: bool = True


    @field_validator("instructions")
    @classmethod
    def _instructions_stripped(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("instructions must not be empty")
        return v


class ResolvedTool(BaseModel):
    """A tool as the runtime sees it: full spec, no further DB lookup needed."""

    model_config = ConfigDict(frozen=True)

    id: str
    key: str
    name: str
    description: str
    input_schema: dict
    output_schema: dict
    risk_class: RiskClass
    approval_policy: ApprovalPolicy
    timeout_ms: int
    idempotency_mode: IdempotencyMode
    # Per-grant config from agent_tool_grants.config, opaque to the runtime.
    grant_config: dict = Field(default_factory=dict)


class ResolvedAgentConfig(BaseModel):
    """What the runtime receives. Snapshot; not for mutation."""

    model_config = ConfigDict(frozen=True)

    agent_id: str
    agent_name: str
    version_id: str
    version: int
    org_id: str
    config: AgentConfig
    tools: tuple[ResolvedTool, ...] = ()
    # Knowledge scope: for MVP, all documents in the org. Collection-based
    # scoping lands later; this field exists so the runtime never has to guess.
    knowledge_scope_all_org_documents: bool = True

