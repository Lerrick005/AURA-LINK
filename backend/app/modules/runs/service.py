"""Read queries for runs and their execution steps.

Every function operates inside the caller's tenant context. Org filter is
also explicit in SQL (defense in depth on top of RLS).
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.runs.schemas import RunDetail, RunList, RunSummary, StepOut


class RunNotFound(Exception):
    """Run is not visible to this org (either it does not exist or RLS blocked)."""


async def list_runs(
    session: AsyncSession,
    *,
    org_id: str,
    workflow_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> RunList:
    where = ["ar.org_id = :org"]
    params: dict[str, Any] = {"org": org_id, "limit": limit, "offset": offset}
    if workflow_id is not None:
        where.append("wv.workflow_id = :wf_id")
        params["wf_id"] = workflow_id
    if status is not None:
        where.append("ar.status = :status")
        params["status"] = status

    where_clause = " AND ".join(where)
    sql = f"""
        SELECT
            ar.id, ar.workflow_version_id, ar.agent_version_id,
            ar.status, ar.terminal_reason, ar.cost_usd,
            ar.tokens_in, ar.tokens_out,
            ar.started_at, ar.finished_at,
            wv.workflow_id, w.name AS workflow_name,
            COUNT(*) OVER () AS total_count
        FROM agent_runs ar
        LEFT JOIN workflow_versions wv ON ar.workflow_version_id = wv.id
        LEFT JOIN workflows w ON wv.workflow_id = w.id
        WHERE {where_clause}
        ORDER BY ar.started_at DESC
        LIMIT :limit OFFSET :offset
    """
    rows = (await session.execute(text(sql), params)).mappings().all()
    total = int(rows[0]["total_count"]) if rows else 0

    items = [
        RunSummary(
            id=r["id"],
            workflow_id=r["workflow_id"],
            workflow_name=r["workflow_name"],
            workflow_version_id=r["workflow_version_id"],
            agent_version_id=r["agent_version_id"],
            status=r["status"],
            terminal_reason=r["terminal_reason"],
            cost_usd=str(r["cost_usd"] or "0"),
            tokens_in=r["tokens_in"] or 0,
            tokens_out=r["tokens_out"] or 0,
            started_at=r["started_at"],
            finished_at=r["finished_at"],
        )
        for r in rows
    ]
    return RunList(items=items, total=total, limit=limit, offset=offset)


async def get_run_detail(
    session: AsyncSession,
    *,
    org_id: str,
    run_id: str,
) -> RunDetail:
    run_row = (await session.execute(
        text("""
            SELECT
                ar.id, ar.workflow_version_id, ar.agent_version_id,
                ar.status, ar.terminal_reason, ar.cost_usd,
                ar.tokens_in, ar.tokens_out,
                ar.input, ar.output,
                ar.started_at, ar.finished_at,
                wv.workflow_id, w.name AS workflow_name
            FROM agent_runs ar
            LEFT JOIN workflow_versions wv ON ar.workflow_version_id = wv.id
            LEFT JOIN workflows w ON wv.workflow_id = w.id
            WHERE ar.id = :id AND ar.org_id = :org
        """),
        {"id": run_id, "org": org_id},
    )).mappings().first()

    if run_row is None:
        raise RunNotFound(run_id)

    step_rows = (await session.execute(
        text("""
            SELECT seq, type, status, node_id, input, output, model,
                   tokens_in, tokens_out, cost_usd, error,
                   started_at, finished_at
            FROM execution_steps
            WHERE execution_id = :id AND org_id = :org
            ORDER BY seq
        """),
        {"id": run_id, "org": org_id},
    )).mappings().all()

    steps = [
        StepOut(
            seq=r["seq"],
            type=r["type"],
            status=r["status"],
            node_id=r["node_id"],
            input=r["input"],
            output=r["output"],
            model=r["model"],
            tokens_in=r["tokens_in"],
            tokens_out=r["tokens_out"],
            cost_usd=str(r["cost_usd"]) if r["cost_usd"] is not None else None,
            error=r["error"],
            started_at=r["started_at"],
            finished_at=r["finished_at"],
        )
        for r in step_rows
    ]

    return RunDetail(
        id=run_row["id"],
        workflow_id=run_row["workflow_id"],
        workflow_name=run_row["workflow_name"],
        workflow_version_id=run_row["workflow_version_id"],
        agent_version_id=run_row["agent_version_id"],
        status=run_row["status"],
        terminal_reason=run_row["terminal_reason"],
        cost_usd=str(run_row["cost_usd"] or "0"),
        tokens_in=run_row["tokens_in"] or 0,
        tokens_out=run_row["tokens_out"] or 0,
        input=run_row["input"],
        output=run_row["output"],
        started_at=run_row["started_at"],
        finished_at=run_row["finished_at"],
        steps=steps,
    )


__all__ = ["list_runs", "get_run_detail", "RunNotFound"]
