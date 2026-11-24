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
import jsonschema
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
from app.modules.runtime.registry import get_tool_registry
from app.modules.runtime.messages import trim_messages
from app.modules.runtime.types import TerminalToolStatus
from app.modules.runtime.decision import RunState, decide_next_step
from dataclasses import dataclass
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
    ToolResult,
    compute_idempotency_key,
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
        self._tool_registry = tool_registry or get_tool_registry()
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
        node_id: str | None = None,
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
                " input, output, model, tokens_in, tokens_out, cost_usd, node_id, finished_at, error) "
                "VALUES "
                "(:id, :o, :e, :v, :seq, :t, :st, "
                " CAST(:inp AS jsonb), CAST(:out AS jsonb), :m, :ti, :to, :c, :node_id, now(), "
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
                "inp": _jsonb(self._redact_step_input(step_type, input_data)),
                "out": _jsonb(self._redact_step_output(step_type, output_data)),
                "m": model,
                "ti": tokens_in,
                "to": tokens_out,
                "c": cost_usd,
                "err": _jsonb(redact(error)) if error else None,
                "node_id": node_id,
            },
        )
        return step_id, seq

    def _redact_step_input(self, step_type: str, data: dict | None) -> dict:
        """Redact step input, preserving fields the engine reads back.

        step types whose input is engine state (read back by later steps)
        keep their structure. step types whose input is user/tool data get
        redacted normally. The distinction is important: redacting
        tool_proposed.input.tool_id makes _step_tool_call unable to
        resolve the tool row and the run fails silently.
        """
        if not data:
            return {}

        if step_type == "tool_proposed":
            # Engine reads tool_id, arguments, call_id, idempotency_key,
            # idempotency_mode, risk_class, approval_policy, tool_timeout_ms.
            # Preserve the whole dict; it is engine state, not user data.
            return dict(data)

        if step_type in ("tool_executing", "tool_result"):
            preserved = {"tool", "call_id", "tool_id"}
            out = {}
            for k, v in data.items():
                out[k] = v if k in preserved else redact(v)
            return out

        if step_type == "tool_rejected":
            preserved = {"tool", "reason", "tool_call_id", "tool_id"}
            out = {}
            for k, v in data.items():
                out[k] = v if k in preserved else redact(v)
            return out

        # Default: redact everything (llm_request, terminated, verified, etc.)
        return redact(data)

    def _redact_step_output(self, step_type: str, data: dict | None) -> dict:
        """Redact step output, preserving structure the engine reads back.

        llm_response.output carries tool_calls[]; the engine extracts
        name/id/arguments from it on the next iteration. Redacting those
        breaks the tool path. We keep the tool_calls array verbatim and
        redact only the content field.
        """
        if not data:
            return {}
        if step_type == "llm_response":
            out = dict(data)
            if isinstance(out.get("content"), str):
                out["content"] = redact(out["content"])
            # tool_calls[] preserved as-is: id, name, arguments are engine state.
            return out
        return redact(data)

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
        if elapsed >= config.limits.max_seconds:
            await self._write_terminated(
                s=s, org_id=org_id, run_id=run_id, agent_version_id=agent_version_id,
                reason="timed_out", detail={"elapsed_seconds": round(elapsed, 2)},
            )
            return "timed_out"
        if row["iteration_count"] >= config.limits.max_steps:
            await self._write_terminated(
                s=s, org_id=org_id, run_id=run_id, agent_version_id=agent_version_id,
                reason="budget_exceeded",
                detail={"limit": "max_steps", "value": config.limits.max_steps},
            )
            return "budget_exceeded"
        if row["tool_call_count"] >= config.limits.max_tool_calls:
            await self._write_terminated(
                s=s, org_id=org_id, run_id=run_id, agent_version_id=agent_version_id,
                reason="budget_exceeded",
                detail={"limit": "max_tool_calls", "value": config.limits.max_tool_calls},
            )
            return "budget_exceeded"
        cost = Decimal(str(row["cost_usd"]))
        if cost >= config.limits.max_cost_usd:
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
        # 4.1-D: validate arguments against the tool input schema before
        # persisting. A schema violation raises EngineError, which
        # _step_tool_call catches and records as a failed tool_call.
        try:
            jsonschema.validate(instance=arguments, schema=tool.input_schema)
        except jsonschema.ValidationError as exc:
            raise EngineError(
                f"tool {tool.key!r} argument validation failed: {exc.message}"
            ) from exc
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
    # --- State loader ----------------------------------------------------
    async def _load_state(
        self, *, run_id: str, org_id: str
    ) -> _LoadedState:
        """Build the frozen RunState for decide_next_step.
        Single short read; no long-held transactions. Returns the last 10
        steps (enough to see the current cycle), counters, run_context
        fields, agent config, and the tri-state terminal-tool status.
        """
        async with tenant_session(org_id) as s:
            run_row = (
                await s.execute(
                    text(
                        "SELECT agent_version_id, iteration_count, tool_call_count, "
                        "       cost_usd, started_at, status "
                        "FROM agent_runs WHERE id = :i"
                    ),
                    {"i": run_id},
                )
            ).mappings().first()
            if run_row is None:
                raise RunNotFound(run_id)
            version_row = (
                await s.execute(
                    text(
                        "SELECT version, config FROM agent_versions WHERE id = :v"
                    ),
                    {"v": run_row["agent_version_id"]},
                )
            ).mappings().first()
            if version_row is None:
                raise EngineError(
                    "agent_version missing for run " + run_id
                )
            config = AgentConfig.model_validate(version_row["config"])
            steps_rows = (
                await s.execute(
                    text(
                        "SELECT seq, type, status FROM execution_steps "
                        "WHERE execution_id = :e ORDER BY seq DESC LIMIT 10"
                    ),
                    {"e": run_id},
                )
            ).mappings().all()
            steps = tuple(reversed(steps_rows))
            ctx_row = (
                await s.execute(
                    text(
                        "SELECT consecutive_rejections, scratch "
                        "FROM run_context WHERE execution_id = :e"
                    ),
                    {"e": run_id},
                )
            ).mappings().first()
            ctx_consecutive_rejections = (
                int(ctx_row["consecutive_rejections"]) if ctx_row else 0
            )
            scratch = (ctx_row["scratch"] if ctx_row else {}) or {}
            nudge_issued = bool(scratch.get("expected_terminal_nudge_issued", False))
            # Pending proposal count: proposals in the most recent
            # llm_response that have not yet been turned into tool_proposed
            # or tool_rejected steps.
            pending_proposals = 0
            last_step_type: str | None = None
            if steps:
                last_step_type = str(steps[-1]["type"])
            # Find most recent llm_response and count proposals made
            # (proposals are in the llm_response step's output.tool_call_count,
            #  if present) minus decisions already recorded after it.
            for row in reversed(steps):
                if row["type"] == "llm_response":
                    output = (await s.execute(
                        text(
                            "SELECT output FROM execution_steps WHERE execution_id = :e AND seq = :sq"
                        ),
                        {"e": run_id, "sq": int(row["seq"])},
                    )).scalar_one()
                    proposed = int((output or {}).get("tool_call_count", 0))
                    decided = sum(
                        1 for r in steps
                        if int(r["seq"]) > int(row["seq"])
                        and r["type"] in ("tool_proposed", "tool_rejected")
                    )
                    pending_proposals = max(0, proposed - decided)
                    break
            terminal_status = await self._terminal_tool_status(
                s=s, run_id=run_id, expected_key=config.expected_terminal_tool
            )
        elapsed = (_now() - run_row["started_at"]).total_seconds()
        return _LoadedState(
            steps=steps,
            counters={
                "iteration_count": int(run_row["iteration_count"]),
                "tool_call_count": int(run_row["tool_call_count"]),
                "cost_usd": float(run_row["cost_usd"]),
                "elapsed_seconds": elapsed,
            },
            consecutive_rejections=ctx_consecutive_rejections,
            nudge_issued=nudge_issued,
            config=config,
            terminal_tool_status=terminal_status,
            pending_proposal_count=pending_proposals,
            last_step_type=last_step_type,
            agent_version_id=str(run_row["agent_version_id"]),
            version=int(version_row["version"]),
            status=str(run_row["status"]),
        )
    async def _terminal_tool_status(
        self,
        *,
        s: AsyncSession,
        run_id: str,
        expected_key: str | None,
    ) -> TerminalToolStatus:
        """Tri-state check on the expected_terminal_tool for this run."""
        if expected_key is None:
            return "not_run"
        row = (
            await s.execute(
                text(
                    "SELECT status, verified FROM tool_calls "
                    "WHERE execution_id = :e AND tool_key_snapshot = :k "
                    "ORDER BY started_at DESC LIMIT 1"
                ),
                {"e": run_id, "k": expected_key},
            )
        ).mappings().first()
        if row is None:
            return "not_run"
        status = str(row["status"])
        verified = row["verified"]
        if status in ("failed", "rejected", "stuck"):
            return "ran_failed"
        if status in ("executing",):
            # Mid-flight: for verification purposes, treat as failed unless
            # the resume pre-check already intervened (which would have
            # escalated earlier).
            return "ran_failed"
        if status == "completed":
            return "ran_ok_verified" if verified is True else "ran_ok_unverified"
        return "not_run"
    # --- Heartbeat -------------------------------------------------------
    @asynccontextmanager
    async def _heartbeat_cancels_loop(
        self,
        *,
        run_id: str,
        org_id: str,
        epoch: int,
        main_task: "asyncio.Task[TerminalReason]",
    ) -> AsyncIterator[None]:
        """Refreshes the lease. On fence loss, cancels the loop task."""
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
                        "heartbeat_lease_lost_cancelling_loop",
                        extra={"run_id": run_id, "epoch": epoch},
                    )
                    main_task.cancel()
                    return
        task = asyncio.create_task(loop())
        try:
            yield
        finally:
            stop.set()
            with suppress(asyncio.CancelledError):
                await task
    # --- Public entry point ---------------------------------------------
    async def run_once(self, *, run_id: str, org_id: str) -> TerminalReason:
        """Drive the run to a terminal state. Idempotent per state rules."""
        import uuid as _uuid
        holder = "worker-" + _uuid.uuid4().hex[:8]
        epoch = await self._claim_lease(run_id=run_id, org_id=org_id, holder=holder)
        # Load agent_version_id early for the resume pre-check
        state0 = await self._load_state(run_id=run_id, org_id=org_id)
        agent_version_id = state0.agent_version_id
        # Resume pre-check runs before the loop.
        try:
            await self._resume_pre_check(
                run_id=run_id, org_id=org_id, agent_version_id=agent_version_id
            )
        except IndeterminateSideEffectError:
            # _resume_pre_check already wrote escalation and terminal.
            # Release lease and re-raise so caller sees the outcome.
            await self._release_lease(run_id=run_id, org_id=org_id, epoch=epoch)
            raise
        loop_task: "asyncio.Task[TerminalReason]" = asyncio.create_task(
            self._loop(run_id=run_id, org_id=org_id, epoch=epoch)
        )
        stale = False
        try:
            async with self._heartbeat_cancels_loop(
                run_id=run_id, org_id=org_id, epoch=epoch, main_task=loop_task
            ):
                return await loop_task
        except asyncio.CancelledError:
            log.info(
                "run_cancelled_by_heartbeat",
                extra={"run_id": run_id, "epoch": epoch},
            )
            raise
        except StaleLeaseError:
            stale = True
            log.warning(
                "run_aborted_stale_lease",
                extra={"run_id": run_id, "epoch": epoch},
            )
            raise
        finally:
            # Release the lease ONLY if we still own this epoch and the run
            # is not in the stale/cancelled path. _release_lease is already
            # epoch-fenced, so a stale caller is a no-op; explicit stale flag
            # documents the intent and avoids any redundant UPDATE.
            if not stale and not loop_task.cancelled():
                with suppress(Exception):
                    await self._release_lease(
                        run_id=run_id, org_id=org_id, epoch=epoch
                    )
    # --- The loop --------------------------------------------------------
    async def _loop(
        self, *, run_id: str, org_id: str, epoch: int
    ) -> TerminalReason:
        while True:
            state = await self._load_state(run_id=run_id, org_id=org_id)
            run_state = RunState(
                steps=state.steps,
                iteration_count=state.counters["iteration_count"],
                tool_call_count=state.counters["tool_call_count"],
                cost_usd=state.counters["cost_usd"],
                elapsed_seconds=state.counters["elapsed_seconds"],
                consecutive_rejections=state.consecutive_rejections,
                nudge_issued=state.nudge_issued,
                config=state.config,
                terminal_tool_status=state.terminal_tool_status,
                pending_proposal_count=state.pending_proposal_count,
                last_step_type=state.last_step_type,
            )
            decision = decide_next_step(run_state)
            if decision.kind == "terminate":
                reason = decision.terminal_reason
                if reason is None:
                    raise EngineError("terminate without terminal_reason")
                async with tenant_session(org_id) as s:
                    await self._fence_and_heartbeat(
                        s=s, run_id=run_id, epoch=epoch
                    )
                    detail: dict[str, Any] = {"from": "decide_next_step"}
                    if reason == "escalated":
                        esc_reason = decision.escalation_reason or "policy_terminate"
                        esc_id = await self._write_escalation(
                            s=s,
                            org_id=org_id,
                            run_id=run_id,
                            agent_version_id=state.agent_version_id,
                            reason=esc_reason,
                            context={
                                "consecutive_rejections": state.consecutive_rejections,
                                "last_step_type": state.last_step_type,
                                "terminal_tool_status": state.terminal_tool_status,
                                "nudge_issued": state.nudge_issued,
                            },
                        )
                        detail["escalation_id"] = esc_id
                        detail["escalation_reason"] = esc_reason
                    await self._write_terminated(
                        s=s,
                        org_id=org_id,
                        run_id=run_id,
                        agent_version_id=state.agent_version_id,
                        reason=reason,
                        detail=detail,
                    )
                return reason
            try:
                if decision.kind == "reasoning":
                    await self._step_reasoning(
                        run_id=run_id,
                        org_id=org_id,
                        epoch=epoch,
                        agent_version_id=state.agent_version_id,
                        config=state.config,
                        nudge=(
                            state.config.expected_terminal_tool is not None
                            and not state.nudge_issued
                            and state.terminal_tool_status == "not_run"
                            and state.last_step_type == "llm_response"
                            and state.pending_proposal_count == 0
                        ),
                    )
                    continue
                if decision.kind == "policy_check":
                    await self._step_policy_check(
                        run_id=run_id,
                        org_id=org_id,
                        epoch=epoch,
                        agent_version_id=state.agent_version_id,
                        config=state.config,
                    )
                    continue
                if decision.kind == "tool_call":
                    await self._step_tool_call(
                        run_id=run_id,
                        org_id=org_id,
                        epoch=epoch,
                        agent_version_id=state.agent_version_id,
                        config=state.config,
                    )
                    continue
                if decision.kind == "verifying":
                    reason = await self._step_verifying(
                        run_id=run_id,
                        org_id=org_id,
                        epoch=epoch,
                        agent_version_id=state.agent_version_id,
                        config=state.config,
                        nudge=decision.post_nudge,
                    )
                    if reason is not None:
                        return reason
                    continue
                raise EngineError("unknown StepKind: " + str(decision.kind))
            except StaleLeaseError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Any step handler exception is a terminal failure, not a
                # stuck run. Per 7, every run must reach a deterministic
                # terminal state. Write terminated(failed) and return.
                async with tenant_session(org_id) as s:
                    await self._fence_and_heartbeat(s=s, run_id=run_id, epoch=epoch)
                    await self._write_terminated(
                        s=s, org_id=org_id, run_id=run_id,
                        agent_version_id=state.agent_version_id,
                        reason="failed",
                        detail={
                            "error_type": type(exc).__name__,
                            "error_message": str(exc)[:500],
                            "from_step": decision.kind,
                        },
                    )
                return "failed"
    # --- Step handlers (filled in Turn 2) -------------------------------
    async def _step_reasoning(
        self,
        *,
        run_id: str,
        org_id: str,
        epoch: int,
        agent_version_id: str,
        config: AgentConfig,
        nudge: bool,
    ) -> None:
        """One LLM call: write intent step, call LLM, write result step.
        No DB transaction is held while the LLM is running. The intent step
        is durable so resume can detect a crash mid-call.
        """
        # ---- Phase 1: build request in a short tx ----
        async with tenant_session(org_id) as s:
            await self._fence_and_heartbeat(s=s, run_id=run_id, epoch=epoch)
            ctx_row = (
                await s.execute(
                    text(
                        "SELECT messages, scratch FROM run_context "
                        "WHERE execution_id = :e"
                    ),
                    {"e": run_id},
                )
            ).mappings().first()
            if ctx_row is None:
                # First reasoning step: seed run_context.
                ctx_id = uuid7str()
                await s.execute(
                    text(
                        "INSERT INTO run_context "
                        "(id, org_id, execution_id, agent_version_id) "
                        "VALUES (:i, :o, :e, :v)"
                    ),
                    {"i": ctx_id, "o": org_id, "e": run_id, "v": agent_version_id},
                )
                existing_messages: list[dict[str, Any]] = []
                scratch: dict[str, Any] = {}
            else:
                existing_messages = list(ctx_row["messages"] or [])
                scratch = dict(ctx_row["scratch"] or {})
            messages = list(existing_messages)
            if not messages:
                messages.append({"role": "system", "content": config.instructions})
                run_input = (
                    await s.execute(
                        text("SELECT input FROM agent_runs WHERE id = :i"),
                        {"i": run_id},
                    )
                ).scalar_one()
                if isinstance(run_input, str):
                    run_input = json.loads(run_input)
                task = (run_input or {}).get("message")
                if not isinstance(task, str) or not task.strip():
                    task = _jsonb(run_input or {})
                messages.append({"role": "user", "content": task})
            if nudge and not scratch.get("expected_terminal_nudge_issued"):
                messages.append({
                    "role": "system",
                    "content": (
                        "You have not yet completed the required action. "
                        "You must call the expected terminal tool before finishing."
                    ),
                })
                scratch["expected_terminal_nudge_issued"] = True
            # Turn messages into LLMMessage objects
            from app.adapters.llm.base import LLMMessage, LLMRequest, ToolSpec
            llm_messages = [
                LLMMessage(
                    role=m["role"],
                    content=m.get("content"),
                )
                for m in messages
            ]
            tools = [
                ToolSpec(
                    name=t.key,
                    description=t.description,
                    input_schema=t.input_schema,
                )
                for t in ()
            ]
            # Build from resolved tools the engine already has via agent config.
            # NOTE: caller passes ResolvedTools; here we only have config.
            # The loop is expected to have run a service resolve before
            # run_once; for MVP we re-resolve tools from agent_tool_grants.
            tool_rows = (
                await s.execute(
                    text(
                        "SELECT t.key, t.description, t.input_schema "
                        "FROM agent_tool_grants g JOIN tools t ON t.id = g.tool_id "
                        "WHERE g.agent_version_id = :v ORDER BY t.key"
                    ),
                    {"v": agent_version_id},
                )
            ).mappings().all()
            tools = [
                ToolSpec(
                    name=r["key"],
                    description=r["description"],
                    input_schema=r["input_schema"],
                )
                for r in tool_rows
            ]
            max_tokens = min(config.max_output_tokens, 1024)
            request = LLMRequest(
                model=config.model,
                messages=llm_messages,
                tools=tools,
                temperature=config.temperature,
                max_output_tokens=max_tokens,
            )
            step_id, _seq = await self._append_step(
                s=s,
                org_id=org_id,
                run_id=run_id,
                agent_version_id=agent_version_id,
                step_type="llm_request",
                status="ok",
                input_data={
                    "model": config.model,
                    "message_count": len(llm_messages),
                    "tool_count": len(tools),
                },
                model=config.model,
            )
            await s.execute(
                text(
                    "UPDATE agent_runs SET iteration_count = iteration_count + 1 "
                    "WHERE id = :i"
                ),
                {"i": run_id},
            )
        # ---- Phase 2: call LLM, no tx held ----
        response = await self._llm.complete(request)
        # ---- Phase 3: persist response in a short tx ----
        cost = self._config.cost_for(
            response.model,
            response.usage.prompt_tokens,
            response.usage.completion_tokens,
        )
        async with tenant_session(org_id) as s:
            await self._fence_and_heartbeat(s=s, run_id=run_id, epoch=epoch)
            await self._append_step(
                s=s,
                org_id=org_id,
                run_id=run_id,
                agent_version_id=agent_version_id,
                step_type="llm_response",
                status="ok",
                input_data={},
                output_data={
                    "content": response.content,
                    "tool_call_count": len(response.tool_calls),
                    "finish_reason": response.finish_reason,
                    "tool_calls": [
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                        for tc in response.tool_calls
                    ],
                },
                model=response.model,
                tokens_in=response.usage.prompt_tokens,
                tokens_out=response.usage.completion_tokens,
                cost_usd=cost,
            )
            new_messages = list(messages)
            new_messages.append(
                {
                    "role": "assistant",
                    "content": response.content,
                    "tool_calls": [
                        {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                        for tc in response.tool_calls
                    ],
                }
            )
            await s.execute(
                text(
                    "UPDATE run_context "
                    "SET messages = CAST(:m AS jsonb), scratch = CAST(:sc AS jsonb), "
                    "    updated_at = now() "
                    "WHERE execution_id = :e"
                ),
                {
                    "m": _jsonb(trim_messages(new_messages)),
                    "sc": _jsonb(scratch),
                    "e": run_id,
                },
            )
            await s.execute(
                text(
                    "UPDATE agent_runs "
                    "SET tokens_in = tokens_in + :ti, tokens_out = tokens_out + :to, "
                    "    cost_usd = cost_usd + :c "
                    "WHERE id = :i"
                ),
                {
                    "ti": response.usage.prompt_tokens,
                    "to": response.usage.completion_tokens,
                    "c": cost,
                    "i": run_id,
                },
            )
    async def _step_policy_check(
        self,
        *,
        run_id: str,
        org_id: str,
        epoch: int,
        agent_version_id: str,
        config: AgentConfig,
    ) -> None:
        """Deterministic checks on pending proposals. Writes tool_proposed or tool_rejected."""
        async with tenant_session(org_id) as s:
            await self._fence_and_heartbeat(s=s, run_id=run_id, epoch=epoch)
            # Get the last llm_response output that has not been fully decided.
            rows = (
                await s.execute(
                    text(
                        "SELECT seq, output FROM execution_steps "
                        "WHERE execution_id = :e AND type = 'llm_response' "
                        "ORDER BY seq DESC LIMIT 1"
                    ),
                    {"e": run_id},
                )
            ).mappings().first()
            if rows is None:
                return
            proposals_raw = (rows["output"] or {}).get("tool_calls", [])
            decided_count = (
                await s.execute(
                    text(
                        "SELECT COUNT(*) FROM execution_steps "
                        "WHERE execution_id = :e AND seq > :sq "
                        "AND type IN ('tool_proposed','tool_rejected')"
                    ),
                    {"e": run_id, "sq": int(rows["seq"])},
                )
            ).scalar_one()
            if int(decided_count) >= len(proposals_raw):
                return
            proposal = proposals_raw[int(decided_count)]
            tool_key = proposal["name"]
            # Look up tool to verify it exists and is granted
            tool_row = (
                await s.execute(
                    text(
                        "SELECT t.id, t.key, t.risk_class, t.approval_policy, "
                        "       t.idempotency_mode, t.input_schema, t.timeout_ms, "
                        "       t.org_id AS tool_org "
                        "FROM tools t "
                        "JOIN agent_tool_grants g ON g.tool_id = t.id "
                        "WHERE g.agent_version_id = :v AND t.key = :k"
                    ),
                    {"v": agent_version_id, "k": tool_key},
                )
            ).mappings().first()
            if tool_row is None:
                await self._reject_proposal(
                    s=s, org_id=org_id, run_id=run_id,
                    agent_version_id=agent_version_id,
                    tool_key=tool_key, tool_call_id=proposal["id"],
                    reason="not_granted", trace_extra={},
                    role_tool_message="Tool not available.",
                )
                return
            try:
                jsonschema.validate(
                    instance=proposal["arguments"], schema=tool_row["input_schema"]
                )
            except jsonschema.ValidationError as exc:
                vpath = "/".join(str(p) for p in exc.absolute_path) or "<root>"
                await self._reject_proposal(
                    s=s, org_id=org_id, run_id=run_id,
                    agent_version_id=agent_version_id,
                    tool_key=tool_key, tool_call_id=proposal["id"],
                    reason="invalid_arguments",
                    trace_extra={"validator": exc.validator, "path": vpath},
                    role_tool_message=(
                        f"Invalid arguments for {tool_key}: "
                        f"'{exc.validator}' failed at {vpath}."
                    ),
                )
                return
            except Exception:
                await self._escalate_misconfigured(
                    s=s, org_id=org_id, run_id=run_id,
                    agent_version_id=agent_version_id, tool_key=tool_key,
                )
                return
            # Compute idempotency key if mode=keyed
            idem_key: str | None = None
            if tool_row["idempotency_mode"] == "keyed":
                idem_key = compute_idempotency_key(
                    execution_id=run_id,
                    agent_version_id=agent_version_id,
                    tool_id=str(tool_row["id"]),
                    arguments=proposal["arguments"],
                )
            # Write the tool_proposed step. Actual execution is the next step.
            await self._append_step(
                s=s,
                org_id=org_id,
                run_id=run_id,
                agent_version_id=agent_version_id,
                step_type="tool_proposed",
                status="ok",
                input_data={
                    "tool": tool_key,
                    "arguments": proposal["arguments"],
                    "risk_class": tool_row["risk_class"],
                    "approval_policy": tool_row["approval_policy"],
                    "idempotency_mode": tool_row["idempotency_mode"],
                    "idempotency_key": idem_key,
                    "tool_id": str(tool_row["id"]),
                    "call_id": proposal["id"],
                    "tool_timeout_ms": int(tool_row["timeout_ms"]),
                },
            )
            # Reset consecutive_rejections on first proposal
            await s.execute(
                text(
                    "UPDATE run_context SET consecutive_rejections = 0, "
                    "updated_at = now() WHERE execution_id = :e"
                ),
                {"e": run_id},
            )
    # --- Rejection and escalation helpers (Patch A) ----------------------
    async def _reject_proposal(
        self,
        *,
        s: AsyncSession,
        org_id: str,
        run_id: str,
        agent_version_id: str,
        tool_key: str,
        tool_call_id: str,
        reason: str,
        trace_extra: dict[str, Any],
        role_tool_message: str | None = None,
    ) -> None:
        """Write tool_rejected, increment consecutive_rejections, and
        append a role=tool message to run_context.messages so the model
        can correct. All in the caller transaction (one fenced tx)."""
        await self._append_step(
            s=s,
            org_id=org_id,
            run_id=run_id,
            agent_version_id=agent_version_id,
            step_type="tool_rejected",
            status="ok",
            input_data={"tool": tool_key, "reason": reason, **trace_extra},
        )
        await s.execute(
            text(
                "UPDATE run_context "
                "SET consecutive_rejections = consecutive_rejections + 1, "
                "    updated_at = now() "
                "WHERE execution_id = :e"
            ),
            {"e": run_id},
        )
        if role_tool_message is not None:
            msg = {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": tool_key,
                "content": role_tool_message,
            }
            # Load, append, trim, rewrite. Same pattern as the other two
            # messages write sites so the trim cap always applies.
            current = (await s.execute(
                text("SELECT messages FROM run_context WHERE execution_id = :e FOR UPDATE"),
                {"e": run_id},
            )).scalar_one()
            new_list = trim_messages(list(current or []) + [msg])
            await s.execute(
                text(
                    "UPDATE run_context "
                    "SET messages = CAST(:m AS jsonb), "
                    "    updated_at = now() "
                    "WHERE execution_id = :e"
                ),
                {"m": _jsonb(new_list), "e": run_id},
            )
    async def _escalate_misconfigured(
        self,
        *,
        s: AsyncSession,
        org_id: str,
        run_id: str,
        agent_version_id: str,
        tool_key: str,
    ) -> str:
        """Escalate tool_misconfigured (bad input_schema or unresolvable
        $ref). Writes escalation and terminated(escalated). Returns esc_id.
        No str(exc), no role=tool message, no consecutive_rejections bump."""
        esc_id = await self._write_escalation(
            s=s,
            org_id=org_id,
            run_id=run_id,
            agent_version_id=agent_version_id,
            reason="tool_misconfigured",
            context={"tool": tool_key},
        )
        await self._write_terminated(
            s=s,
            org_id=org_id,
            run_id=run_id,
            agent_version_id=agent_version_id,
            reason="escalated",
            detail={"reason": "tool_misconfigured", "escalation_id": esc_id},
        )
        return esc_id
    async def _escalate_indeterminate(
        self,
        *,
        s: AsyncSession,
        org_id: str,
        run_id: str,
        agent_version_id: str,
        stuck_call_id: str,
    ) -> str:
        """Mark the tool_calls row stuck, write indeterminate_side_effect
        escalation, return esc_id. Caller writes terminated and decides
        whether to raise (resume path) or return (loop catch-all)."""
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
        return esc_id
    async def _load_full_tool(
        self, *, s: AsyncSession, tool_id: str
    ) -> ResolvedTool:
        """Load the full tool row for executor-side schema validation (4.1-D).
        The proposal snapshot in execution_steps.input carries only the fields
        that _step_policy_check persisted, which did not include the schemas.
        Re-read the row inside the caller transaction so the executor sees
        a real input_schema and can validate arguments before any I/O.
        """
        row = (
            await s.execute(
                text(
                    "SELECT id, key, name, description, input_schema, output_schema, "
                    "       risk_class, approval_policy, timeout_ms, idempotency_mode "
                    "FROM tools WHERE id = :tid"
                ),
                {"tid": tool_id},
            )
        ).mappings().first()
        if row is None:
            raise EngineError(f"tool {tool_id!r} no longer exists")
        return ResolvedTool(
            id=row["id"],
            key=row["key"],
            name=row["name"],
            description=row["description"],
            input_schema=row["input_schema"],
            output_schema=row["output_schema"],
            risk_class=row["risk_class"],
            approval_policy=row["approval_policy"],
            timeout_ms=int(row["timeout_ms"]),
            idempotency_mode=row["idempotency_mode"],
            grant_config={},
        )
    async def _step_tool_call(
        self,
        *,
        run_id: str,
        org_id: str,
        epoch: int,
        agent_version_id: str,
        config: AgentConfig,
    ) -> None:
        """Execute one approved proposal. Writes tool_executing then tool_result."""
        # Phase 1: read the proposal, persist intent (or detect a prior attempt)
        async with tenant_session(org_id) as s:
            await self._fence_and_heartbeat(s=s, run_id=run_id, epoch=epoch)
            last_proposed = (
                await s.execute(
                    text(
                        "SELECT id, seq, input FROM execution_steps "
                        "WHERE execution_id = :e AND type = 'tool_proposed' "
                        "ORDER BY seq DESC LIMIT 1"
                    ),
                    {"e": run_id},
                )
            ).mappings().first()
            if last_proposed is None:
                raise EngineError("no tool_proposed step to execute")
            proposed_input = last_proposed["input"]
            proposal_step_id = str(last_proposed["id"])
            tool = await self._load_full_tool(
                s=s, tool_id=proposed_input["tool_id"]
            )
            try:
                executor = self._tool_registry.executor_for(tool.key)
            except EngineError:
                await self._escalate_misconfigured(
                    s=s, org_id=org_id, run_id=run_id,
                    agent_version_id=agent_version_id, tool_key=tool.key,
                )
                return

            prior = (
                await s.execute(
                    text(
                        "SELECT id, status, idempotency_mode_snapshot "
                        "FROM tool_calls "
                        "WHERE execution_id = :e AND step_id = :sid"
                    ),
                    {"e": run_id, "sid": proposal_step_id},
                )
            ).mappings().first()

            if prior is None:
                await self._append_step(
                    s=s, org_id=org_id, run_id=run_id,
                    agent_version_id=agent_version_id,
                    step_type="tool_executing", status="executing",
                    input_data={"tool": proposed_input["tool"]},
                )
                call_id = await self._persist_tool_call(
                    s=s, org_id=org_id, run_id=run_id,
                    step_id=proposal_step_id,
                    agent_version_id=agent_version_id,
                    tool=tool,
                    arguments=proposed_input["arguments"],
                    idempotency_key=proposed_input.get("idempotency_key"),
                    status="executing",
                )
            elif prior["status"] == "stuck":
                return
            elif prior["status"] in ("completed", "failed"):
                raise EngineError("tool_call finalized without tool_result step")
            elif prior["idempotency_mode_snapshot"] == "keyed":
                call_id = str(prior["id"])
            else:
                esc_id = await self._escalate_indeterminate(
                    s=s, org_id=org_id, run_id=run_id,
                    agent_version_id=agent_version_id,
                    stuck_call_id=str(prior["id"]),
                )
                await self._write_terminated(
                    s=s, org_id=org_id, run_id=run_id,
                    agent_version_id=agent_version_id,
                    reason="escalated",
                    detail={
                        "reason": "indeterminate_side_effect",
                        "escalation_id": esc_id,
                    },
                )
                return
        # Phase 2: execute tool with timeout, NO tx held
        # SECURITY FIX: Timeout enforced from tool config, not LLM proposal.
        timeout_s = float(tool.timeout_ms) / 1000.0 if tool.timeout_ms else 15.0
        timed_out = False
        try:
            result = await asyncio.wait_for(
                executor(
                    tool=tool,
                    arguments=proposed_input["arguments"],
                    idempotency_key=proposed_input.get("idempotency_key"),
                    timeout_seconds=timeout_s,
                ),
                timeout=timeout_s + 5.0,
            )
        except asyncio.TimeoutError:
            timed_out = True
            result = ToolResult(ok=False, error=TimeoutError("tool timeout"))
        except Exception as exc:
            result = ToolResult(ok=False, error=exc)
        # Phase 3: run post_check if present (no tx held)
        verified: bool | None = result.verified
        post_check_exc: BaseException | None = None
        if result.ok:
            post_check = self._tool_registry.post_check_for(tool.key)
            if post_check is not None:
                try:
                    verified = await post_check(
                        proposed_input["arguments"], result.output
                    )
                except Exception as exc:
                    post_check_exc = exc
                    verified = None
        # Phase 4: persist result in a short tx
        async with tenant_session(org_id) as s:
            await self._fence_and_heartbeat(s=s, run_id=run_id, epoch=epoch)
            if timed_out and tool.idempotency_mode != "keyed":
                esc_id = await self._escalate_indeterminate(
                    s=s, org_id=org_id, run_id=run_id,
                    agent_version_id=agent_version_id,
                    stuck_call_id=str(call_id),
                )
                await self._write_terminated(
                    s=s, org_id=org_id, run_id=run_id,
                    agent_version_id=agent_version_id,
                    reason="escalated",
                    detail={"reason": "indeterminate_side_effect",
                            "escalation_id": esc_id},
                )
                return
            call_status = "completed" if result.ok else "failed"
            await s.execute(
                text(
                    "UPDATE tool_calls "
                    "SET status = :st, output = CAST(:out AS jsonb), "
                    "    output_redacted = true, verified = :v, finished_at = now(), "
                    "    error = CAST(:err AS jsonb) "
                    "WHERE id = :i"
                ),
                {
                    "st": call_status,
                    "out": _jsonb(redact(result.output)) if result.ok else None,
                    "v": verified,
                    "err": (
                        _jsonb(redact(_error_dict(result.error)))
                        if result.error else None
                    ),
                    "i": call_id,
                },
            )
            await self._append_step(
                s=s,
                org_id=org_id,
                run_id=run_id,
                agent_version_id=agent_version_id,
                step_type="tool_result",
                status="ok" if result.ok else "failed",
                input_data={"tool": tool.key},
                output_data=redact(result.output) if result.ok else {},
                error=(
                    redact(_error_dict(result.error)) if result.error else None
                ),
            )
            await s.execute(
                text(
                    "UPDATE agent_runs SET tool_call_count = tool_call_count + 1 "
                    "WHERE id = :i"
                ),
                {"i": run_id},
            )
            # Update messages to include the tool result turn
            ctx_row = (
                await s.execute(
                    text("SELECT messages FROM run_context WHERE execution_id = :e"),
                    {"e": run_id},
                )
            ).mappings().first()
            if ctx_row is not None:
                messages = list(ctx_row["messages"] or [])
                messages.append(
                    {
                        "role": "tool",
                        "content": (
                            _jsonb(result.output) if result.ok
                            else _jsonb(_error_dict(result.error))
                        ),
                        "tool_call_id": proposed_input.get("call_id"),
                        "name": tool.key,
                    }
                )
                await s.execute(
                    text(
                        "UPDATE run_context SET messages = CAST(:m AS jsonb), "
                        "updated_at = now() WHERE execution_id = :e"
                    ),
                    {"m": _jsonb(trim_messages(messages)), "e": run_id},
                )
            # Post-check exception escalation for external/irreversible
            if post_check_exc is not None and tool.risk_class in (
                "external_communication", "irreversible_write"
            ):
                esc_id = await self._write_escalation(
                    s=s,
                    org_id=org_id,
                    run_id=run_id,
                    agent_version_id=agent_version_id,
                    reason="verification_error",
                    context={
                        "tool_call_id": call_id,
                        "error_type": type(post_check_exc).__name__,
                    },
                    tool_call_id=call_id,
                )
                await self._write_terminated(
                    s=s, org_id=org_id, run_id=run_id,
                    agent_version_id=agent_version_id,
                    reason="escalated",
                    detail={
                        "reason": "verification_error",
                        "escalation_id": esc_id,
                    },
                )
    async def _step_verifying(
        self,
        *,
        run_id: str,
        org_id: str,
        epoch: int,
        agent_version_id: str,
        config: AgentConfig,
        nudge: bool,
    ) -> TerminalReason | None:
        """Final outcome check. Writes terminated(completed) or escalates."""
        async with tenant_session(org_id) as s:
            await self._fence_and_heartbeat(s=s, run_id=run_id, epoch=epoch)
            expected = config.expected_terminal_tool
            satisfied = False
            if expected is not None:
                row = (
                    await s.execute(
                        text(
                            "SELECT status, verified FROM tool_calls "
                            "WHERE execution_id = :e AND tool_key_snapshot = :k "
                            "ORDER BY started_at DESC LIMIT 1"
                        ),
                        {"e": run_id, "k": expected},
                    )
                ).mappings().first()
                if row is not None and row["status"] == "completed":
                    if not config.require_verified_terminal or row["verified"] is True:
                        satisfied = True
            if expected is not None and not satisfied:
                await self._append_step(
                    s=s, org_id=org_id, run_id=run_id,
                    agent_version_id=agent_version_id,
                    step_type="verified", status="failed",
                    input_data={"expected": expected, "satisfied": False},
                )
                esc_id = await self._write_escalation(
                    s=s, org_id=org_id, run_id=run_id,
                    agent_version_id=agent_version_id,
                    reason="expected_tool_not_executed",
                    context={"expected_tool": expected},
                )
                await self._write_terminated(
                    s=s, org_id=org_id, run_id=run_id,
                    agent_version_id=agent_version_id,
                    reason="escalated",
                    detail={
                        "reason": "expected_tool_not_executed",
                        "escalation_id": esc_id,
                    },
                )
                return "escalated"
            await self._append_step(
                s=s, org_id=org_id, run_id=run_id,
                agent_version_id=agent_version_id,
                step_type="verified", status="ok",
                input_data={"expected": expected, "satisfied": satisfied},
            )
            await self._write_terminated(
                s=s, org_id=org_id, run_id=run_id,
                agent_version_id=agent_version_id,
                reason="completed",
            )
            return "completed"
@dataclass(frozen=True)
class _LoadedState:
    steps: tuple[Any, ...]
    counters: dict[str, Any]
    consecutive_rejections: int
    nudge_issued: bool
    config: AgentConfig
    terminal_tool_status: TerminalToolStatus
    pending_proposal_count: int
    last_step_type: str | None
    agent_version_id: str
    version: int
    status: str
__all__ = ["RuntimeEngine"]
def _error_dict(exc: BaseException | None) -> dict[str, Any]:
    if exc is None:
        return {}
    return {
        "error_type": type(exc).__name__,
        "message": str(exc),
    }






