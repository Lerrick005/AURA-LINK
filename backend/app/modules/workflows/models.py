"""ORM models for workflows.

Both tables have org_id and RLS (see migration 0007). Queries must use
tenant_session(org_id), same as every other tenant-scoped table.

Design:
- Workflow is the definition (mutable metadata + which version is current).
- WorkflowVersion is the immutable graph snapshot. Created, never updated.
  Deploying flips workflows.current_version_id.
- Versions use MAX(version)+1 within the caller's transaction, matching
  the agents.service.create_version pattern.
- The FK from workflow_versions.workflow_id -> workflows.id is CASCADE.
  Deleting a workflow deletes its versions, which is correct: a version
  has no meaning without its parent.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.modules.agents.models import Base


def _uuid7_pk() -> str:
    from app.core.ids import uuid7str
    return uuid7str()


class Workflow(Base):
    __tablename__ = "workflows"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid7_pk)
    org_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="draft")
    current_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("org_id", "name", name="uq_workflows_org_name"),
        Index("ix_workflows_org_status", "org_id", "status"),
        Index("ix_workflows_org_created", "org_id", "created_at"),
    )


class WorkflowVersion(Base):
    """Immutable. A new version is created for every graph change."""

    __tablename__ = "workflow_versions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid7_pk)
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("workflows.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    org_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    graph: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("workflow_id", "version",
                         name="uq_workflow_versions_workflow_version"),
        Index("ix_workflow_versions_org_workflow",
              "org_id", "workflow_id"),
    )
