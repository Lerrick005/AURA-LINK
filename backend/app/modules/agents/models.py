"""SQLAlchemy models for the agent engine (Milestone 2 first slice).

Design notes:
- All primary keys are UUIDv7 strings (app-generated via app.core.ids). The
  Postgres column type is VARCHAR(36), not UUID, because app.org_id GUC is set
  as text and comparisons against a uuid column would need casts. Trade-off:
  36 bytes per PK vs 16; irrelevant at our scale, and it removes a class of
  type-cast surprises in RLS policies.
- agent_versions are immutable: no updated_at. Service layer refuses UPDATE.
- Agent configuration is JSONB. Pydantic validates at the service boundary;
  the DB stores it opaquely.
- agent_runs is the first slice of the executions table in blueprint §11.2.
  execution_steps and tool_calls land with the runtime state machine.
- RLS is enforced in the Alembic migration, not in the model. Every table with
  org_id gets ENABLE + FORCE ROW LEVEL SECURITY + policy using
  current_setting('app.org_id'). A CI test enumerates tables and fails if any
  is missing RLS.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base for all ORM models. Metadata is shared so Alembic can autogenerate."""


def _uuid7_pk() -> str:
    # Local import to avoid a circular import at module load time.
    from app.core.ids import uuid7str
    return uuid7str()


class Agent(Base):
    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid7_pk)
    org_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_agents_org_name"),
        Index("ix_agents_org_status", "org_id", "status"),
    )


class AgentVersion(Base):
    """Immutable. A new version is created for every deploy; no UPDATEs."""

    __tablename__ = "agent_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid7_pk)
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    org_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)

    __table_args__ = (
        UniqueConstraint("agent_id", "version", name="uq_agent_versions_agent_version"),
        Index("ix_agent_versions_org_agent", "org_id", "agent_id"),
    )


class Tool(Base):
    """Builtin tools have org_id NULL; per-org tools land later."""

    __tablename__ = "tools"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid7_pk)
    org_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    input_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    output_schema: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    risk_class: Mapped[str] = mapped_column(String(32), nullable=False)
    # read_only | reversible_write | irreversible_write | financial | external_communication
    approval_policy: Mapped[str] = mapped_column(String(32), nullable=False, default="auto")
    # auto | always | above_threshold
    timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=15000)
    idempotency_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="keyed")
    # none | keyed | tool_managed
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("org_id", "key", name="uq_tools_org_key"),
        Index("ix_tools_org_risk", "org_id", "risk_class"),
    )


class AgentToolGrant(Base):
    """Per-version allowlist. Absence of a grant means the agent cannot call the tool."""

    __tablename__ = "agent_tool_grants"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid7_pk)
    agent_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_versions.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    tool_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tools.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    org_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("agent_version_id", "tool_id", name="uq_grants_version_tool"),
    )


class AgentRun(Base):
    """First slice of executions. Runtime state machine extends this later."""

    __tablename__ = "agent_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid7_pk)
    org_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    agent_version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("agent_versions.id", ondelete="RESTRICT"), nullable=False, index=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    # pending | running | completed | failed | escalated | cancelled | timed_out | budget_exceeded
    terminal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    input: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    output: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    iteration_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tool_call_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_usd: Mapped[float] = mapped_column(Numeric(12, 6), nullable=False, default=0)

    __table_args__ = (
        Index("ix_agent_runs_org_status", "org_id", "status"),
        Index("ix_agent_runs_org_started", "org_id", "started_at"),
    )
