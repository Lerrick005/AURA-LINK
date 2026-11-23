
"""_smoke_escalation.py - verify the terminate-with-escalation fix.

Scenario: agent has one real tool granted, expected_terminal_tool = that tool.
The stub LLM never calls the real tool; instead it proposes a phantom tool
"phantom.send" three times in a row. Precedence 2 in decide_next_step fires
(consecutive_rejections >= 3) and terminate(reason="escalated") runs with
escalation_reason="tool_rejections_exceeded".

Assertions (read from a FRESH session after run_once returns):
  1. agent_runs.terminal_reason == 'escalated'
  2. >=3 execution_steps with type='tool_rejected' and input->>'reason'='not_granted'
  3. exactly 1 escalations row for run_id, reason='tool_rejections_exceeded'
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
from app.modules.runtime.types import EngineConfig


ORG_ID = "00000000-0000-7000-8000-000000000001"


class PhantomToolLLM:
    """Stub LLM. Every complete() proposes a tool that does not exist."""

    name = "stub_phantom"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        tc = ToolCall(
            id=f"call_{self.calls}",
            name="phantom.send",
            arguments={"to": "x@example.com", "body": "hi"},
        )
        return LLMResponse(
            content=None,
            tool_calls=(tc,),
            finish_reason="tool_calls",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
            model=request.model,
            provider="stub_phantom",
        )

    async def stream(self, request):
        raise NotImplementedError


class NeverCalledRegistry:
    """ToolRegistry stub; must never be reached in this scenario."""

    def executor_for(self, tool_key):
        raise AssertionError(
            f"executor_for({tool_key!r}) called - should not happen"
        )

    def post_check_for(self, tool_key):
        raise AssertionError(
            f"post_check_for({tool_key!r}) called - should not happen"
        )


async def seed_fixture() -> tuple[str, str, str]:
    """Create tool + agent + version + grant + deploy + pending run row.

    Returns (agent_id, agent_version_id, run_id).
    """
    tool_id = uuid7str()

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
                "key": "real.read",
                "name": "Real Read",
                "desc": "A real tool that the agent should call but will not.",
                "inp": '{"type":"object"}',
                "out": '{"type":"object"}',
            },
        )

    async with tenant_session(ORG_ID) as s:
        agent = await create_agent(
            s, org_id=ORG_ID, name=f"smoke-{uuid7str()[:8]}"
        )
        agent_id = agent.id
        config = AgentConfig(
            instructions="You must call real.read to complete the task.",
            model="gpt-4o-mini",
            limits=AgentLimits(
                max_steps=8,
                max_tool_calls=4,
                max_seconds=60,
                max_cost_usd=Decimal("1.00"),
            ),
            expected_terminal_tool="real.read",
            require_verified_terminal=True,
        )
        version = await create_version(
            s, org_id=ORG_ID, agent_id=agent_id, config=config
        )
        agent_version_id = version.id
        await grant_tools(
            s,
            org_id=ORG_ID,
            agent_version_id=agent_version_id,
            grants=[(tool_id, {})],
        )
        await deploy_version(
            s,
            org_id=ORG_ID,
            agent_id=agent_id,
            agent_version_id=agent_version_id,
        )

    run_id = uuid7str()
    async with tenant_session(ORG_ID) as s:
        await s.execute(
            text(
                "INSERT INTO agent_runs (id, org_id, agent_version_id, status, input) "
                "VALUES (:id, :o, :v, 'pending', CAST(:i AS jsonb))"
            ),
            {
                "id": run_id,
                "o": ORG_ID,
                "v": agent_version_id,
                "i": '{"task":"smoke"}',
            },
        )

    return agent_id, agent_version_id, run_id


async def main() -> int:
    _agent_id, _version_id, run_id = await seed_fixture()

    engine = RuntimeEngine(
        session_factory=get_sessionmaker(),
        llm=PhantomToolLLM(),
        tool_registry=NeverCalledRegistry(),
        config=EngineConfig(
            lease_ttl_seconds=30, heartbeat_interval_seconds=5
        ),
    )

    reason = await engine.run_once(run_id=run_id, org_id=ORG_ID)
    print(f"run_once returned: {reason}")

    async with tenant_session(ORG_ID) as s:
        dbname = (
            await s.execute(text("SELECT current_database()"))
        ).scalar_one()
        print(f"asserting against database: {dbname}")

        run = (
            await s.execute(
                text(
                    "SELECT status, terminal_reason FROM agent_runs WHERE id=:i"
                ),
                {"i": run_id},
            )
        ).mappings().one()
        print(f"agent_runs: {dict(run)}")

        rejected = (
            await s.execute(
                text(
                    "SELECT count(*) FROM execution_steps "
                    "WHERE execution_id=:e AND type='tool_rejected' "
                    "AND input->>'reason'='not_granted'"
                ),
                {"e": run_id},
            )
        ).scalar_one()
        print(f"tool_rejected(not_granted) rows: {rejected}")

        esc = (
            await s.execute(
                text(
                    "SELECT id, reason, context, status FROM escalations "
                    "WHERE execution_id=:e"
                ),
                {"e": run_id},
            )
        ).mappings().all()
        print(f"escalations rows: {len(esc)}")
        for r in esc:
            print(f"  {dict(r)}")

        steps = (
            await s.execute(
                text(
                    "SELECT seq, type, status FROM execution_steps "
                    "WHERE execution_id=:e ORDER BY seq"
                ),
                {"e": run_id},
            )
        ).mappings().all()
        print(f"execution_steps: {len(steps)} rows")
        for st in steps:
            print(f"  {st['seq']:>3} {st['type']:<20} {st['status']}")

        fails: list[str] = []
        if run["terminal_reason"] != "escalated":
            fails.append(
                f"terminal_reason={run['terminal_reason']!r}, expected 'escalated'"
            )
        if rejected < 3:
            fails.append(f"tool_rejected count={rejected}, expected >=3")
        if len(esc) != 1:
            fails.append(f"escalations count={len(esc)}, expected 1")
        elif esc[0]["reason"] != "tool_rejections_exceeded":
            fails.append(
                f"escalation reason={esc[0]['reason']!r}, "
                "expected 'tool_rejections_exceeded'"
            )

        if fails:
            print("\nFAIL:")
            for f in fails:
                print(f"  - {f}")
            return 1
        print("\nPASS: escalation row written and verified from a fresh session")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))