"""Runtime engine: state machine for governed agent execution.

Part A: infrastructure. Part B (next file section) adds run_once() and the
step handlers. This file owns transaction boundaries, lease fencing, and
the single write path for execution_steps and tool_calls.

Fence rule: the first statement in every transactional block is
  UPDATE agent_runs SET lease_heartbeat_at=now()
   WHERE id=:run_id AND lease_epoch=:epoch
If 0 rows, raise StaleLeaseError. No side effects applied.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.llm.base import LLMProvider
from app.core.db import tenant_session
from app.core.ids import uuid7str
from app.core.logging import get_logger
from app.core.redaction import redact
from app.modules.agents.schemas import AgentConfig, ResolvedTool
from app.modules.runtime.types import (
    EngineConfig,
    EngineError,
    IndeterminateSideEffectError,
    RunNotFound,
    RunNotResumable,
    StaleLeaseError,
    TerminalReason,
    ToolRegistry,
)

log = get_logger(__name__)


TERMINAL_REASONS: frozenset[str] = frozenset(
    {"completed", "failed", "escalated", "timed_out", "budget_exceeded", "cancelled"}
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _jsonb(value: Any) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))


class RuntimeEngine:
    """Executes one agent run to a terminal state."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        llm: LLMProvider,
        tool_registry: ToolRegistry,
        config: EngineConfig,
    ) -> None:
        self._session_factory = session_factory
        self._llm = llm
        self._tool_registry = tool_registry
        self._config = config

    # --- Leases ---------------------------------------------------------

    async def _claim_lease(
        self, *, run_id: str, org_id: str, holder: str
    ) -> int:
        """Claim or renew the lease. Returns the new epoch."""
        async with tenant_session(org_id) as s:
            row = (
                await s.execute(
                    text(
                        "SELECT status, lease_holder, lease_expires_at, lease_epoch "
                        "FROM agent_runs WHERE id = :i FOR UPDATE"
                    ),
                    {"i": run_id},
                )
            ).mappings().first()
            if row is None:
                raise RunNotFound(run_id)
            if row["status"] in TERMINAL_REASONS:
                raise RunNotResumable(
                    "run " + run_id + " is terminal: " + str(row["status"])
                )
            now = _now()
            held_by_other = (
                row["lease_holder"] is not None
                and row["lease_holder"] != holder
                and row["lease_expires_at"] is not None
                and row["lease_expires_at"] > now
            )
            if held_by_other:
                raise StaleLeaseError(
                    "run " + run_id + " leased to " + str(row["lease_holder"])
                    + " until " + str(row["lease_expires_at"])
                )
            new_epoch = int(row["lease_epoch"]) + 1
            await s.execute(
                text(
                    "UPDATE agent_runs "
                    "SET lease_holder = :h, lease_epoch = :e, "
                    "    lease_expires_at = now() + make_interval(secs => :ttl), "
                    "    lease_heartbeat_at = now(), "
                    "    status = CASE WHEN status = 'pending' THEN 'running' ELSE status END "
                    "WHERE id = :i"
                ),
                {"h": holder, "e": new_epoch, "ttl": self._config.lease_ttl_seconds, "i": run_id},
            )
            return new_epoch

    async def _fence_and_heartbeat(
        self, *, s: AsyncSession, run_id: str, epoch: int
    ) -> None:
        """First statement of every transactional block. Raises on stale epoch."""
        result = await s.execute(
            text(
                "UPDATE agent_runs "
                "SET lease_heartbeat_at = now(), "
                "    lease_expires_at = now() + make_interval(secs => :ttl) "
                "WHERE id = :i AND lease_epoch = :e AND status = 'running' "
                "RETURNING id"
            ),
            {"ttl": self._config.lease_ttl_seconds, "i": run_id, "e": epoch},
        )
        if result.first() is None:
            raise StaleLeaseError(
                "epoch " + str(epoch) + " no longer current for run " + run_id
            )

    async def _release_lease(
        self, *, run_id: str, org_id: str, epoch: int
    ) -> None:
        async with tenant_session(org_id) as s:
            await s.execute(
                text(
                    "UPDATE agent_runs SET lease_holder = NULL, lease_expires_at = NULL "
                    "WHERE id = :i AND lease_epoch = :e"
                ),
                {"i": run_id, "e": epoch},
            )

    # --- Step writing (single path) ------------------------------------

    async def _append_step(
        self,
        *,
        s: AsyncSession,
        org_id: str,
        run_id: str,
        agent_version_id: str,
        step_type: str,
        status: str,
        input_data: dict[str, Any] | None = None,
        output_data: dict[str, Any] | None = None,
        model: str | None = None,
        tokens_in: int | None = None,
        tokens_out: int | None = None,
        cost_usd: Decimal | None = None,
        error: dict[str, Any] | None = None,
    ) -> tuple[str, int]:
        seq = int(
            (
                await s.execute(
                    text(
                        "SELECT COALESCE(MAX(seq), 0) + 1 "
                        "FROM execution_steps WHERE execution_id = :e"
                    ),
                    {"e": run_id},
                )
            ).scalar_one()
        )
        step_id = uuid7str()
        await s.execute(
            text(
                "INSERT INTO execution_steps "
                "(id, org_id, execution_id, agent_version_id, seq, type, status, "
                " input, output, model, tokens_in, tokens_out, cost_usd, finished_at, error) "
                "VALUES "
                "(:id, :o, :e, :v, :seq, :t, :st, "
                " CAST(:inp AS jsonb), CAST(:out AS jsonb), :m, :ti, :to, :c, now(), "
                " CAST(:err AS jsonb))"
            ),
            {
                "id": step_id,
                "o": org_id,
                "e": run_id,
                "v": agent_version_id,
                "seq": seq,
                "t": step_type,
                "st": status,
                "inp": _jsonb(redact(input_data or {})),
                "out": _jsonb(redact(output_data or {})),
                "m": model,
                "ti": tokens_in,
                "to": tokens_out,
                "c": cost_usd,
                "err": _jsonb(redact(error)) if error else None,
            },
        )
        return step_id, seq

    # --- Budget / time -------------------------------------------------

    async def _budget_or_terminal(
        self,
        *,
        s: AsyncSession,
        run_id: str,
        org_id: str,
        agent_version_id: str,
        config: AgentConfig,
    ) -> TerminalReason | None:
        row = (
            await s.execute(
                text(
                    "SELECT iteration_count, tool_call_count, tokens_in, tokens_out, "
                    "       cost_usd, started_at "
                    "FROM agent_runs WHERE id = :i"
                ),
                {"i": run_id},
            )
        ).mappings().one()

        now = _now()
        elapsed = (now - row["started_at"]).total_seconds()
        if elapsed > config.limits.max_seconds:
            await self._write_terminated(
                s=s, org_id=org_id, run_id=run_id, agent_version_id=agent_version_id,
                reason="timed_out", detail={"elapsed_seconds": round(elapsed, 2)},
            )
            return "timed_out"

        if row["iteration_count"] > config.limits.max_steps:
            await self._write_terminated(
                s=s, org_id=org_id, run_id=run_id, agent_version_id=agent_version_id,
                reason="budget_exceeded",
                detail={"limit": "max_steps", "value": config.limits.max_steps},
            )
            return "budget_exceeded"

        if row["tool_call_count"] > config.limits.max_tool_calls:
            await self._write_terminated(
                s=s, org_id=org_id, run_id=run_id, agent_version_id=agent_version_id,
                reason="budget_exceeded",
                detail={"limit": "max_tool_calls", "value": config.limits.max_tool_calls},
            )
            return "budget_exceeded"

        cost = Decimal(str(row["cost_usd"]))
        if cost > config.limits.max_cost_usd:
            await self._write_terminated(
                s=s, org_id=org_id, run_id=run_id, agent_version_id=agent_version_id,
                reason="budget_exceeded",
                detail={
                    "limit": "max_cost_usd",
                    "value": str(config.limits.max_cost_usd),
                    "actual": str(cost),
                },
            )
            return "budget_exceeded"

        return None

    # --- Terminal -----------------------------------------------------

    async def _write_terminated(
        self,
        *,
        s: AsyncSession,
        org_id: str,
        run_id: str,
        agent_version_id: str,
        reason: str,
        detail: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
    ) -> None:
        if reason not in TERMINAL_REASONS:
            raise EngineError("invalid terminal reason: " + reason)

        await self._append_step(
            s=s, org_id=org_id, run_id=run_id, agent_version_id=agent_version_id,
            step_type="terminated", status=reason,
            input_data={"reason": reason, "detail": detail or {}},
            output_data=redact(output) if output else None,
        )

        await s.execute(
            text(
                "UPDATE agent_runs "
                "SET status = :st, terminal_reason = :r, finished_at = now(), "
                "    lease_holder = NULL, lease_expires_at = NULL, "
                "    output = CAST(:out AS jsonb) "
                "WHERE id = :i"
            ),
            {
                "st": reason,
                "r": reason,
                "out": _jsonb(redact(output)) if output else None,
                "i": run_id,
            },
        )

    async def _write_escalation(
        self,
        *,
        s: AsyncSession,
        org_id: str,
        run_id: str,
        agent_version_id: str,
        reason: str,
        context: dict[str, Any],
        tool_call_id: str | None = None,
    ) -> str:
        esc_id = uuid7str()
        await s.execute(
            text(
                "INSERT INTO escalations "
                "(id, org_id, execution_id, agent_version_id, tool_call_id, "
                " reason, priority, context, status) "
                "VALUES (:id, :o, :e, :v, :tc, :r, 'normal', CAST(:ctx AS jsonb), 'open')"
            ),
            {
                "id": esc_id,
                "o": org_id,
                "e": run_id,
                "v": agent_version_id,
                "tc": tool_call_id,
                "r": reason,
                "ctx": _jsonb(redact(context)),
            },
        )
        return esc_id

    # --- Resume pre-check ----------------------------------------------

    async def _resume_pre_check(
        self, *, run_id: str, org_id: str, agent_version_id: str
    ) -> None:
        async with tenant_session(org_id) as s:
            row = (
                await s.execute(
                    text(
                        "SELECT id FROM tool_calls "
                        "WHERE execution_id = :e AND status = 'executing' "
                        "  AND idempotency_mode_snapshot = 'none' "
                        "  AND idempotency_key IS NULL "
                        "LIMIT 1"
                    ),
                    {"e": run_id},
                )
            ).first()
            if row is not None:
                stuck_call_id = row[0]
                await s.execute(
                    text("UPDATE tool_calls SET status = 'stuck' WHERE id = :i"),
                    {"i": stuck_call_id},
                )
                esc_id = await self._write_escalation(
                    s=s,
                    org_id=org_id,
                    run_id=run_id,
                    agent_version_id=agent_version_id,
                    reason="indeterminate_side_effect",
                    context={"stuck_tool_call_id": stuck_call_id},
                    tool_call_id=stuck_call_id,
                )
                await self._write_terminated(
                    s=s,
                    org_id=org_id,
                    run_id=run_id,
                    agent_version_id=agent_version_id,
                    reason="escalated",
                    detail={
                        "reason": "indeterminate_side_effect",
                        "escalation_id": esc_id,
                    },
                )
                raise IndeterminateSideEffectError(stuck_call_id)

    # --- Heartbeat ------------------------------------------------------

    @asynccontextmanager
    async def _heartbeat(
        self, *, run_id: str, org_id: str, epoch: int
    ) -> AsyncIterator[None]:
        stop = asyncio.Event()

        async def loop() -> None:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(
                        stop.wait(),
                        timeout=self._config.heartbeat_interval_seconds,
                    )
                    return
                except asyncio.TimeoutError:
                    pass
                try:
                    async with tenant_session(org_id) as s:
                        await self._fence_and_heartbeat(
                            s=s, run_id=run_id, epoch=epoch
                        )
                except StaleLeaseError:
                    log.warning(
                        "heartbeat_lost_lease",
                        extra={"run_id": run_id, "epoch": epoch},
                    )
                    return

        task = asyncio.create_task(loop())
        try:
            yield
        finally:
            stop.set()
            with suppress(asyncio.CancelledError):
                await task

    # --- Persist a tool call (single writer) --------------------------

    async def _persist_tool_call(
        self,
        *,
        s: AsyncSession,
        org_id: str,
        run_id: str,
        step_id: str,
        agent_version_id: str,
        tool: ResolvedTool,
        arguments: dict[str, Any],
        idempotency_key: str | None,
        status: str,
    ) -> str:
        call_id = uuid7str()
        await s.execute(
            text(
                "INSERT INTO tool_calls "
                "(id, org_id, execution_id, step_id, agent_version_id, tool_id, "
                " tool_key_snapshot, risk_class_snapshot, idempotency_mode_snapshot, "
                " input, input_redacted, status, idempotency_key, escalation_id) "
                "VALUES "
                "(:id, :o, :e, :sp, :v, :t, :tk, :rc, :im, CAST(:inp AS jsonb), true, "
                " :st, :ik, :esc)"
            ),
            {
                "id": call_id,
                "o": org_id,
                "e": run_id,
                "sp": step_id,
                "v": agent_version_id,
                "t": tool.id,
                "tk": tool.key,
                "rc": tool.risk_class,
                "im": tool.idempotency_mode,
                "inp": _jsonb(redact(arguments)),
                "st": status,
                "ik": idempotency_key,
                "esc": None,
            },
        )
        return call_id


__all__ = ["RuntimeEngine"]
