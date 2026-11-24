"""WorkflowEngine: executes a workflow run to a terminal state.

Inherits lease, fencing, heartbeat, step-write, and terminal-state primitives
from RuntimeEngine (Stage 2). Overrides the loop and state loader to dispatch
on workflow nodes instead of agent steps.

Node handlers implemented here:
  - trigger: mark input received, transition to next
  - agent:   create sub-run via agents.service.start_run, call
             RuntimeEngine.run_once on the sub-run, propagate outcome
  - terminate: write terminal step, terminate the workflow

Condition and tool node handlers are NOT in this file - they ship in
Stage 3.1d and 3.1e respectively. If the engine encounters one, it raises
EngineError("node type not yet supported: X"). That failure becomes a
workflow-run escalation.
"""
from __future__ import annotations

import asyncio
import uuid

import jsonschema
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm.base import LLMProvider
from app.core.db import get_sessionmaker, tenant_session
from app.core.ids import uuid7str
from app.core.logging import get_logger
from app.modules.agents.schemas import ResolvedTool
from app.modules.agents.service import start_run
from app.modules.runtime.engine import RuntimeEngine
from app.modules.runtime.types import EngineConfig, EngineError, TerminalReason
from app.modules.workflows.decision import decide_next_node
from app.modules.workflows.expression import ExpressionError, evaluate
from app.modules.workflows.schemas import Node, NodeConfig, NodeType, WorkflowGraph
from app.modules.workflows.types import (
    NodeDecision,
    NodeExec,
    WorkflowState,
    TERMINAL_ESCALATED,
    TERMINAL_FAILED,
    TERMINAL_COMPLETED,
)

log = get_logger(__name__)


class WorkflowEngineError(Exception):
    """Base for workflow engine errors."""


class WorkflowNotFound(WorkflowEngineError):
    pass


class WorkflowNotDeployed(WorkflowEngineError):
    pass


class WorkflowEngine(RuntimeEngine):
    """Executes one workflow run to a terminal state.

    Composes the same primitives as the agent engine but dispatches on
    workflow nodes. Inherits:
      - _claim_lease / _release_lease / _fence_and_heartbeat
      - _append_step / _write_terminated / _write_escalation
      - _heartbeat_cancels_loop
    Overrides:
      - _load_state  (returns WorkflowState, not RunState)
      - _loop        (dispatches workflow nodes, not agent steps)
    Adds:
      - _step_trigger / _step_agent / _step_terminate
    """

    def __init__(
        self,
        *,
        session_factory,
        llm: LLMProvider,
        config: EngineConfig,
        tool_registry,
        agent_engine: RuntimeEngine,
    ) -> None:
        super().__init__(
            session_factory=session_factory,
            llm=llm,
            config=config,
            tool_registry=tool_registry,
        )
        self._agent_engine = agent_engine

    # --- Entry point -----------------------------------------------------

    async def run_once(self, *, run_id: str, org_id: str) -> TerminalReason:
        """Drive the workflow run to a terminal state. Idempotent per state rules."""
        holder = "wf-worker-" + uuid.uuid4().hex[:8]
        epoch = await self._claim_lease(run_id=run_id, org_id=org_id, holder=holder)

        state0 = await self._load_state(run_id=run_id, org_id=org_id)
        if state0.workflow_version_id is None:
            await self._release_lease(run_id=run_id, org_id=org_id, epoch=epoch)
            raise WorkflowNotDeployed(
                f"run {run_id!r} has no workflow_version_id"
            )

        loop_task: asyncio.Task[TerminalReason] = asyncio.create_task(
            self._loop(run_id=run_id, org_id=org_id, epoch=epoch)
        )
        stale = False
        try:
            async with self._heartbeat_cancels_loop(
                run_id=run_id, org_id=org_id, epoch=epoch, main_task=loop_task,
            ):
                return await loop_task
        except asyncio.CancelledError:
            log.info("wf_run_cancelled", extra={"run_id": run_id, "epoch": epoch})
            raise
        except Exception:
            stale = True
            log.exception("wf_run_failed", extra={"run_id": run_id, "epoch": epoch})
            raise
        finally:
            if not stale and not loop_task.cancelled():
                try:
                    await self._release_lease(run_id=run_id, org_id=org_id, epoch=epoch)
                except Exception:
                    log.exception("wf_release_lease_failed", extra={"run_id": run_id})

    # --- The loop --------------------------------------------------------

    async def _loop(self, *, run_id: str, org_id: str, epoch: int) -> TerminalReason:
        while True:
            loaded = await self._load_state(run_id=run_id, org_id=org_id)
            state = loaded.workflow_state
            decision = decide_next_node(state)

            if decision.kind == "terminate":
                reason = decision.terminal_reason or TERMINAL_FAILED
                async with tenant_session(org_id) as s:
                    await self._fence_and_heartbeat(s=s, run_id=run_id, epoch=epoch)
                    await self._write_terminated(
                        s=s,
                        org_id=org_id,
                        run_id=run_id,
                        agent_version_id=loaded.agent_version_id,
                        reason=reason,
                        detail={
                            "node_count": state.node_count,
                            "agent_calls": state.agent_call_count,
                            "terminal_detail": decision.terminal_detail,
                        },
                    )
                return reason

            # run_node dispatch
            node = next(
                (n for n in state.graph.nodes if n.id == decision.node_id),
                None,
            )
            if node is None:
                async with tenant_session(org_id) as s:
                    await self._fence_and_heartbeat(s=s, run_id=run_id, epoch=epoch)
                    await self._write_terminated(
                        s=s, org_id=org_id, run_id=run_id,
                        agent_version_id=loaded.agent_version_id,
                        reason=TERMINAL_FAILED,
                        detail={"error": f"node {decision.node_id!r} not in graph"},
                    )
                return TERMINAL_FAILED

            try:
                if node.type.value == "trigger":
                    await self._step_trigger(
                        run_id=run_id, org_id=org_id, epoch=epoch,
                        agent_version_id=loaded.agent_version_id,
                        node=node,
                    )
                elif node.type.value == "agent":
                    await self._step_agent(
                        run_id=run_id, org_id=org_id, epoch=epoch,
                        agent_version_id=loaded.agent_version_id,
                        node=node,
                        workflow_version_id=loaded.workflow_version_id,
                    )
                elif node.type.value == "terminate":
                    async with tenant_session(org_id) as s:
                        await self._fence_and_heartbeat(s=s, run_id=run_id, epoch=epoch)
                        reason = node.config.reason if node.config else TERMINAL_COMPLETED
                        await self._write_terminated(
                            s=s, org_id=org_id, run_id=run_id,
                            agent_version_id=loaded.agent_version_id,
                            reason=reason,
                            detail={"node_id": node.id},
                        )
                    return reason
                elif node.type.value == "condition":
                    await self._step_condition(
                        run_id=run_id, org_id=org_id, epoch=epoch,
                        agent_version_id=loaded.agent_version_id,
                        node=node,
                    )
                elif node.type.value == "tool":
                    await self._step_tool(
                        run_id=run_id, org_id=org_id, epoch=epoch,
                        agent_version_id=loaded.agent_version_id,
                        node=node,
                    )
                else:
                    raise WorkflowEngineError(
                        f"node type not yet supported: {node.type.value!r}"
                    )
            except WorkflowEngineError as exc:
                log.error(
                    "wf_node_dispatch_failed",
                    extra={"run_id": run_id, "node_id": node.id, "error": str(exc)},
                )
                async with tenant_session(org_id) as s:
                    await self._fence_and_heartbeat(s=s, run_id=run_id, epoch=epoch)
                    await self._write_terminated(
                        s=s, org_id=org_id, run_id=run_id,
                        agent_version_id=loaded.agent_version_id,
                        reason=TERMINAL_ESCALATED,
                        detail={"node_id": node.id, "error": str(exc)},
                    )
                return TERMINAL_ESCALATED

    # --- Node handlers ---------------------------------------------------

    # --- Redaction override ---------------------------------------------

    def _redact_step_output(self, step_type: str, data: dict | None) -> dict:
        """Condition steps write engine-internal routing decisions.

        `chosen_next_node_id` is read back by decide_next_node on the next
        cycle. Redacting it makes the graph unroutable, so we preserve the
        entire condition-step output verbatim.
        """
        if step_type == "condition" and data is not None:
            return data
        return super()._redact_step_output(step_type, data)

    # --- Node handlers ---------------------------------------------------

    async def _step_trigger(
        self,
        *,
        run_id: str,
        org_id: str,
        epoch: int,
        agent_version_id: str,
        node,
    ) -> None:
        """Record the trigger. The input is already on agent_runs.input."""
        async with tenant_session(org_id) as s:
            await self._fence_and_heartbeat(s=s, run_id=run_id, epoch=epoch)
            input_row = (await s.execute(
                text("SELECT input FROM agent_runs WHERE id = :i"),
                {"i": run_id},
            )).scalar_one()
            await self._append_step(
                s=s, org_id=org_id, run_id=run_id,
                agent_version_id=agent_version_id,
                step_type="trigger",
                status="ok",
                input_data={"node_id": node.id},
                output_data={"input": input_row or {}},
                node_id=node.id,
            )

    async def _step_agent(
        self,
        *,
        run_id: str,
        org_id: str,
        epoch: int,
        agent_version_id: str,
        node,
        workflow_version_id: str,
    ) -> None:
        """Create an agent sub-run, execute it, propagate the outcome."""
        if not node.config or not node.config.agent_id:
            raise WorkflowEngineError(f"agent node {node.id!r} missing agent_id")
        agent_id = node.config.agent_id

        # Load workflow input as sub-run task
        async with tenant_session(org_id) as s:
            await self._fence_and_heartbeat(s=s, run_id=run_id, epoch=epoch)
            wf_input = (await s.execute(
                text("SELECT input FROM agent_runs WHERE id = :i"),
                {"i": run_id},
            )).scalar_one()
            # Start sub-run in this session (flushes but doesn't commit)
            sub_input = {"task": wf_input, "parent_run_id": run_id, "node_id": node.id}
            sub_run_id = await start_run(
                s, org_id=org_id, agent_id=agent_id,
                input_data=sub_input,
                workflow_version_id=workflow_version_id,
            )

        # Run the sub-run via the agent engine (its own lease, own steps)
        terminal = await self._agent_engine.run_once(
            run_id=sub_run_id, org_id=org_id,
        )

        # Read sub-run result
        async with tenant_session(org_id) as s:
            await self._fence_and_heartbeat(s=s, run_id=run_id, epoch=epoch)
            sub_result = (await s.execute(
                text(
                    "SELECT status, terminal_reason, output, cost_usd, "
                    "       iteration_count, tool_call_count "
                    "FROM agent_runs WHERE id = :i"
                ),
                {"i": sub_run_id},
            )).mappings().one()

            await self._append_step(
                s=s, org_id=org_id, run_id=run_id,
                agent_version_id=agent_version_id,
                step_type="agent",
                status="ok" if terminal == TERMINAL_COMPLETED else "failed",
                input_data={
                    "node_id": node.id,
                    "agent_id": agent_id,
                    "sub_run_id": sub_run_id,
                },
                output_data={
                    "terminal": terminal,
                    "sub_run_output": sub_result["output"],
                    "sub_run_cost": str(sub_result["cost_usd"]),
                },
                node_id=node.id,
            )

            # Aggregate cost from sub-run into parent
            await s.execute(
                text(
                    "UPDATE agent_runs "
                    "SET cost_usd = cost_usd + :c, "
                    "    tokens_in = tokens_in + COALESCE("
                    "      (SELECT tokens_in FROM agent_runs WHERE id = :sub), 0), "
                    "    tokens_out = tokens_out + COALESCE("
                    "      (SELECT tokens_out FROM agent_runs WHERE id = :sub), 0) "
                    "WHERE id = :i"
                ),
                {"c": sub_result["cost_usd"], "sub": sub_run_id, "i": run_id},
            )

        if terminal != TERMINAL_COMPLETED:
            # Sub-run failed, escalated, timed out, etc. Propagate as workflow terminal.
            # The loop's exception handler owns the single terminal write.
            # Writing here AND raising causes a double _write_terminated,
            # which sets status='escalated' and then StaleLeaseError on the second fence.
            raise WorkflowEngineError(
                f"agent node {node.id!r} sub-run terminal={terminal}"
            )
            # Signal the loop to exit by raising — the loop writes terminated
            # but only if it sees terminal state; cleaner to raise a sentinel.
            # For MVP: return and let the loop detect the written terminated step.
            # Loop returns because last_node_id will still be prior trigger;
            # the state still has last_node_id = trigger. So we escalate here.
            raise WorkflowEngineError(
                f"agent node {node.id!r} sub-run terminal={terminal}"
            )

    async def _step_condition(
        self,
        *,
        run_id: str,
        org_id: str,
        epoch: int,
        agent_version_id: str,
        node,
    ) -> None:
        """Evaluate condition branches in order, record the chosen path.

        The first branch whose `when` expression evaluates truthy is taken.
        If no branch matches (e.g. no catch-all with `when: True`), the
        condition is a graph design error and we escalate.
        """
        async with tenant_session(org_id) as s:
            await self._fence_and_heartbeat(s=s, run_id=run_id, epoch=epoch)
            run = (await s.execute(
                text("SELECT input FROM agent_runs WHERE id = :i"),
                {"i": run_id},
            )).mappings().one()
            context = run["input"] or {}

            if not node.branches:
                raise WorkflowEngineError(
                    f"condition node {node.id!r}: no branches"
                )

            chosen: str | None = None
            evaluation_log: list[dict] = []
            for i, branch in enumerate(node.branches):
                try:
                    result = evaluate(branch.when, context)
                except ExpressionError as exc:
                    raise WorkflowEngineError(
                        f"condition node {node.id!r} branch {i}: {exc}"
                    ) from exc
                evaluation_log.append({"branch_index": i, "result": bool(result)})
                if result:
                    chosen = branch.next
                    break

            if chosen is None:
                raise WorkflowEngineError(
                    f"condition node {node.id!r}: no branch matched "
                    f"(evaluated {len(evaluation_log)} branch(es))"
                )

            await self._append_step(
                s=s, org_id=org_id, run_id=run_id,
                agent_version_id=agent_version_id,
                step_type="condition",
                status="ok",
                input_data={"node_id": node.id, "context": context},
                output_data={
                    "node_id": node.id,
                    "chosen_next_node_id": chosen,
                    "evaluations": evaluation_log,
                },
                node_id=node.id,
            )

    # --- Tool resolution + dispatch --------------------------------------

    async def _resolve_tool_by_key(
        self, *, s: AsyncSession, org_id: str, tool_key: str
    ) -> ResolvedTool:
        """Resolve a tool by its key, respecting org scope.

        Mirrors RuntimeEngine._load_full_tool but queries by key instead of id
        (workflow nodes store tool_key, not tool_id). Prefers org-scoped tools
        over global (org_id IS NULL) when both share a key.
        """
        row = (await s.execute(
            text(
                "SELECT id, key, name, description, input_schema, output_schema, "
                "       risk_class, approval_policy, timeout_ms, idempotency_mode "
                "FROM tools "
                "WHERE key = :k AND (org_id IS NULL OR org_id = :o) "
                "ORDER BY CASE WHEN org_id = :o THEN 0 ELSE 1 END "
                "LIMIT 1"
            ),
            {"k": tool_key, "o": org_id},
        )).mappings().first()
        if row is None:
            raise WorkflowEngineError(
                f"tool_key not found or not visible to org: {tool_key!r}"
            )
        return ResolvedTool(
            id=row["id"],
            key=row["key"],
            name=row["name"],
            description=row["description"] or "",
            input_schema=row["input_schema"] or {},
            output_schema=row["output_schema"] or {},
            risk_class=row["risk_class"],
            approval_policy=row["approval_policy"],
            timeout_ms=int(row["timeout_ms"]),
            idempotency_mode=row["idempotency_mode"],
            grant_config={},
        )

    async def _step_tool(
        self,
        *,
        run_id: str,
        org_id: str,
        epoch: int,
        agent_version_id: str,
        node,
    ) -> None:
        """Execute a tool node.

        MVP scope:
          - Arguments = the entire workflow input (jsonb on agent_runs.input).
            Workflow authors transform inputs via upstream nodes; a per-node
            args_map will arrive in a later stage.
          - Does NOT write a tool_calls row. execution_steps is the audit
            surface for workflow tool invocations. Cross-run tool analytics
            via tool_calls is a Stage 3.5 concern (see ADR-007, TBD).
          - Idempotency key is `wf:{run_id}:{node.id}` — stable across
            crash-resume of the same workflow run, distinct across runs.
        """
        if not node.config or not node.config.tool_key:
            raise WorkflowEngineError(
                f"tool node {node.id!r} missing config.tool_key"
            )
        tool_key = node.config.tool_key

        # Phase 1: resolve tool, validate arguments, fetch executor (in txn)
        async with tenant_session(org_id) as s:
            await self._fence_and_heartbeat(s=s, run_id=run_id, epoch=epoch)
            run = (await s.execute(
                text("SELECT input FROM agent_runs WHERE id = :i"),
                {"i": run_id},
            )).mappings().one()
            arguments = run["input"] or {}

            tool = await self._resolve_tool_by_key(
                s=s, org_id=org_id, tool_key=tool_key,
            )

            try:
                jsonschema.validate(instance=arguments, schema=tool.input_schema)
            except jsonschema.ValidationError as exc:
                raise WorkflowEngineError(
                    f"tool {tool.key!r} argument validation failed: {exc.message}"
                ) from exc

            try:
                executor = self._tool_registry.executor_for(tool.key)
            except EngineError as exc:
                raise WorkflowEngineError(
                    f"tool {tool.key!r} not registered with an executor: {exc}"
                ) from exc

        # Phase 2: invoke executor (outside any transaction; executor manages its own I/O)
        idempotency_key = f"wf:{run_id}:{node.id}"
        try:
            result = await executor(
                tool=tool,
                arguments=arguments,
                idempotency_key=idempotency_key,
                timeout_seconds=tool.timeout_ms / 1000.0,
            )
        except Exception as exc:
            raise WorkflowEngineError(
                f"tool {tool.key!r} executor raised: {exc}"
            ) from exc

        # Phase 3: persist step, raise on failure (loop turns it into escalate)
        async with tenant_session(org_id) as s:
            await self._fence_and_heartbeat(s=s, run_id=run_id, epoch=epoch)
            await self._append_step(
                s=s, org_id=org_id, run_id=run_id,
                agent_version_id=agent_version_id,
                step_type="tool",
                status="ok" if result.ok else "failed",
                input_data={
                    "node_id": node.id,
                    "tool_key": tool.key,
                    "arguments": arguments,
                },
                output_data={
                    "node_id": node.id,
                    "ok": result.ok,
                    "result": result.output,
                    "verified": result.verified,
                    "error": (
                        str(result.error) if result.error is not None else None
                    ),
                },
                node_id=node.id,
            )

        if not result.ok:
            raise WorkflowEngineError(
                f"tool {tool.key!r} returned ok=False: {result.error}"
            )

    # --- State loader ----------------------------------------------------

    async def _load_state(self, *, run_id: str, org_id: str) -> "_LoadedWorkflowState":
        async with tenant_session(org_id) as s:
            run = (await s.execute(
                text(
                    "SELECT agent_version_id, workflow_version_id, status, "
                    "       input, cost_usd, started_at "
                    "FROM agent_runs WHERE id = :i"
                ),
                {"i": run_id},
            )).mappings().one_or_none()
            if run is None:
                raise WorkflowNotFound(run_id)

            wv_id = run["workflow_version_id"]
            if wv_id is None:
                # Not a workflow run. Return a stub with empty graph; run_once
                # will raise WorkflowNotDeployed before using it.
                stub = WorkflowGraph(
                    entry_node_id="_none",
                    nodes=[Node(id="_none", type=NodeType.TERMINATE,
                                config=NodeConfig(reason="failed"))],
                )
                return _LoadedWorkflowState(
                    agent_version_id=run["agent_version_id"],
                    workflow_version_id=None,
                    workflow_state=WorkflowState(
                        graph=stub,
                        last_node_id=None,
                        node_executions=(),
                        node_count=0,
                        agent_call_count=0,
                        tool_call_count=0,
                        cost_usd=run["cost_usd"] or 0,
                        elapsed_seconds=0.0,
                    ),
                )

            ver = (await s.execute(
                text("SELECT graph FROM workflow_versions WHERE id = :i"),
                {"i": wv_id},
            )).scalar_one_or_none()
            if ver is None:
                raise WorkflowEngineError(
                    f"workflow_version {wv_id!r} not found for run {run_id!r}"
                )
            graph = WorkflowGraph.model_validate(ver)

            step_rows = (await s.execute(
                text(
                    "SELECT node_id, type, status, output, started_at "
                    "FROM execution_steps "
                    "WHERE execution_id = :e AND node_id IS NOT NULL "
                    "ORDER BY seq"
                ),
                {"e": run_id},
            )).mappings().all()

            execs: list[NodeExec] = []
            for row in step_rows:
                output = row["output"]
                execs.append(NodeExec(
                    node_id=row["node_id"],
                    node_type=row["type"],
                    status=row["status"],
                    output=output,
                    started_at=row["started_at"].timestamp() if row["started_at"] else 0.0,
                ))

            last_node_id = execs[-1].node_id if execs else None
            now_ts = (
                await s.execute(text("SELECT EXTRACT(EPOCH FROM now())"))
            ).scalar_one()
            started_ts = (
                run["started_at"].timestamp() if run["started_at"] else now_ts
            )

            agent_calls = sum(1 for e in execs if e.node_type == "agent")
            tool_calls = 0  # tool nodes ship in 3.1e

            return _LoadedWorkflowState(
                agent_version_id=run["agent_version_id"],
                workflow_version_id=wv_id,
                workflow_state=WorkflowState(
                    graph=graph,
                    last_node_id=last_node_id,
                    node_executions=tuple(execs),
                    node_count=len(execs),
                    agent_call_count=agent_calls,
                    tool_call_count=tool_calls,
                    cost_usd=run["cost_usd"] or 0,
                    elapsed_seconds=max(0.0, float(now_ts) - started_ts),
                ),
            )


class _LoadedWorkflowState:
    """Container returned by _load_state. Mirrors _LoadedState's role."""

    __slots__ = ("agent_version_id", "workflow_version_id", "workflow_state")

    def __init__(
        self,
        *,
        agent_version_id: str,
        workflow_version_id: str | None,
        workflow_state: WorkflowState,
    ) -> None:
        self.agent_version_id = agent_version_id
        self.workflow_version_id = workflow_version_id
        self.workflow_state = workflow_state


__all__ = ["WorkflowEngine", "WorkflowEngineError", "WorkflowNotFound", "WorkflowNotDeployed"]


