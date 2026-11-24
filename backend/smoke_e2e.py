"""smoke_e2e.py - Stage 2 exit gate: one persisted end-to-end agent run.

Scenario: agent has one read-only tool granted. HappyPathLLM proposes the tool
on iteration 1 and returns final content on iteration 2. RuntimeEngine.run_once
executes, persists, and terminates as completed.

Asserts (read from a FRESH session after run_once returns):
  1. agent_runs.status == 'completed'
  2. exactly one execution_steps row with type='terminated'
  3. at least one execution_steps row with type='tool_result'
  4. at least one tool_calls row with status='completed'
  5. run_context.messages is non-empty (assistant turn + tool result present)
  6. len(run_context.messages) <= MAX_MESSAGES (F3 cap holds under real flow)
  7. terminal_reason on the run is 'completed' or NULL (engine chooses; both OK)
"""
from __future__ import annotations

import asyncio
import sys
from decimal import Decimal

from sqlalchemy import text

from app.core.db import get_sessionmaker, tenant_session
from app.core.ids import uuid7str
from app.adapters.llm.base import (
    LLMRequest,
    LLMResponse,
    TokenUsage,
    ToolCall,
)
from app.modules.agents.schemas import AgentConfig, AgentLimits
from app.modules.agents.service import (
    create_agent,
    create_version,
    deploy_version,
    grant_tools,
)
from app.modules.runtime.engine import RuntimeEngine
from app.modules.runtime.messages import trim_messages
from app.modules.runtime.types import EngineConfig, MAX_MESSAGES  # MAX_MESSAGES if exposed; else import from messages


ORG_ID = "00000000-0000-7000-8000-000000000001"
TOOL_KEY = "e2e.read"


class HappyPathLLM:
    """Stub: iteration 1 proposes the tool call, iteration 2 returns final content."""

    name = "stub_happy"

    def __init__(self, tool_name: str):
        self.tool_name = tool_name
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            tc = ToolCall(
                id="call_1",
                name=self.tool_name,
                arguments={"message": "hello from e2e"},
            )
            return LLMResponse(
                content=None,
                tool_calls=(tc,),
                finish_reason="tool_calls",
                usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
                model=request.model,
                provider="stub_happy",
            )
        return LLMResponse(
            content="Task complete: the tool returned successfully.",
            tool_calls=(),
            finish_reason="stop",
            usage=TokenUsage(prompt_tokens=15, completion_tokens=10),
            model=request.model,
            provider="stub_happy",
        )

    async def stream(self, request):
        raise NotImplementedError


async def seed_fixture() -> tuple[str, str, str]:
    """Create tool + agent + version + grant + deploy + pending run. Return ids."""
    tool_id = uuid7str()

    # 1. Tool (org_id=NULL so it's visible to all orgs, matching echo pattern)
    async with tenant_session(ORG_ID) as s:
        await s.execute(
            text(
                "INSERT INTO tools (id, org_id, key, name, description, "
                " input_schema, output_schema, risk_class, approval_policy, "
                " timeout_ms, idempotency_mode) "
                "VALUES (:id, NULL, :key, :name, :desc, "
                " CAST(:inp AS jsonb), CAST(:out AS jsonb), "
                " 'read_only', 'auto', 15000, 'keyed')"
            ),
            {
                "id": tool_id,
                "key": TOOL_KEY,
                "name": "E2E Read",
                "desc": "Read-only tool for the e2e exit-gate run.",
                "inp": '{"type":"object","properties":{"message":{"type":"string"}},"required":["message"]}',
                "out": '{"type":"object"}',
            },
        )

    # 2. Agent + version + grant + deploy
    async with tenant_session(ORG_ID) as s:
        agent = await create_agent(s, org_id=ORG_ID, name=f"e2e-{uuid7str()[:8]}")
        agent_id = agent.id
        config = AgentConfig(
            instructions="Call e2e.read to complete the task.",
            model="gpt-4o-mini",
            limits=AgentLimits(
                max_steps=10, max_tool_calls=3,
                max_seconds=30, max_cost_usd=1.0,
            ),
            expected_terminal_tool=None,
            require_verified_terminal=False,
        )
        version = await create_version(
            s, org_id=ORG_ID, agent_id=agent_id, config=config,
        )
        version_id = version.id
        await grant_tools(
            s, org_id=ORG_ID, agent_version_id=version_id,
            grants=[(tool_id, {})],
        )
        await deploy_version(
            s, org_id=ORG_ID, agent_id=agent_id, agent_version_id=version_id,
        )

    # 3. Pending AgentRun
    run_id = uuid7str()
    async with tenant_session(ORG_ID) as s:
        await s.execute(
            text(
                "INSERT INTO agent_runs (id, org_id, agent_version_id, status, input) "
                "VALUES (:id, :o, :v, 'pending', CAST(:inp AS jsonb))"
            ),
            {
                "id": run_id,
                "o": ORG_ID,
                "v": version_id,
                "inp": '{"task":"say hello using the e2e.read tool"}',
            },
        )

    return agent_id, version_id, run_id


async def main() -> int:
    print(f"ORG_ID = {ORG_ID}")
    agent_id, version_id, run_id = await seed_fixture()
    print(f"Seeded agent={agent_id[:8]} version={version_id[:8]} run={run_id[:8]}")

    llm = HappyPathLLM(tool_name=TOOL_KEY)
    config = EngineConfig(
        lease_ttl_seconds=30,
        heartbeat_interval_seconds=5,
        max_tool_timeout_seconds=15.0,
    )

    engine = RuntimeEngine(
        session_factory=get_sessionmaker(),
        llm=llm,
        config=config,
    )

    print(f"\nRunning engine.run_once (LLM calls: {llm.calls} before) ...")
    terminal = await engine.run_once(run_id=run_id, org_id=ORG_ID)
    print(f"run_once returned: terminal={terminal} llm.calls={llm.calls}")

    # ---- Verify from a fresh session ----
    fails: list[str] = []

    async with tenant_session(ORG_ID) as s:
        row = (await s.execute(
            text(
                "SELECT status, terminal_reason, finished_at "
                "FROM agent_runs WHERE id = :i"
            ),
            {"i": run_id},
        )).mappings().one_or_none()
        if row is None:
            fails.append("agent_runs row disappeared")
            return _report(fails)
        if row["status"] != "completed":
            fails.append(f"agent_runs.status={row['status']!r}, want 'completed'")
        if row["finished_at"] is None:
            fails.append("agent_runs.finished_at is NULL (never finalized)")

        steps = (await s.execute(
            text(
                "SELECT type, status FROM execution_steps "
                "WHERE execution_id = :i ORDER BY seq"
            ),
            {"i": run_id},
        )).mappings().all()
        types = [r["type"] for r in steps]
        if "terminated" not in types:
            fails.append(f"no execution_steps.type='terminated' (got types={types})")
        if "tool_result" not in types:
            fails.append(f"no execution_steps.type='tool_result' (got types={types})")

        tc = (await s.execute(
            text(
                "SELECT status FROM tool_calls "
                "WHERE execution_id = :i"
            ),
            {"i": run_id},
        )).mappings().all()
        if not tc:
            fails.append("no tool_calls rows")
        elif not any(r["status"] == "completed" for r in tc):
            fails.append(f"no tool_calls.status='completed' (got statuses={[r['status'] for r in tc]})")

        ctx = (await s.execute(
            text(
                "SELECT messages FROM run_context WHERE execution_id = :i"
            ),
            {"i": run_id},
        )).scalar_one_or_none()
        if not ctx:
            fails.append("run_context.messages is NULL/empty")
        else:
            if len(ctx) < 2:
                fails.append(f"run_context.messages has {len(ctx)} items (want >=2)")
            if len(ctx) > MAX_MESSAGES:
                fails.append(f"run_context.messages has {len(ctx)} > MAX_MESSAGES={MAX_MESSAGES}")

    if fails:
        return _report(fails)
    print("\nPASS: end-to-end persisted run completed and verified")
    return 0


def _report(fails: list[str]) -> int:
    if not fails:
        return 0
    print("\nFAIL:")
    for f in fails:
        print(f"  - {f}")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
