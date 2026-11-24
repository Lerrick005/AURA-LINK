"""smoke_e2e.py - Stage 2 exit gate: one persisted end-to-end agent run."""
from __future__ import annotations

import asyncio
import sys

from sqlalchemy import text

from app.core.db import get_sessionmaker, tenant_session
from app.core.ids import uuid7str
from app.adapters.llm.base import (
    LLMRequest, LLMResponse, TokenUsage, ToolCall,
)
from app.modules.agents.schemas import AgentConfig, AgentLimits
from app.modules.agents.service import (
    create_agent, create_version, deploy_version, grant_tools,
)
from app.modules.runtime.engine import RuntimeEngine
from app.modules.runtime.registry import get_tool_registry
from app.modules.runtime.types import EngineConfig
from app.modules.tools.echo import register_echo_tool


ORG_ID = "00000000-0000-7000-8000-000000000001"
TOOL_KEY = "echo"


class HappyPathLLM:
    name = "stub_happy"

    def __init__(self, tool_name):
        self.tool_name = tool_name
        self.calls = 0

    async def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            tc = ToolCall(id="call_1", name=self.tool_name,
                          arguments={"message": "hello from e2e"})
            return LLMResponse(
                content=None, tool_calls=(tc,),
                finish_reason="tool_calls",
                usage=TokenUsage(prompt_tokens=10, completion_tokens=5),
                model=request.model, provider="stub_happy",
            )
        return LLMResponse(
            content="Task complete: the tool returned successfully.",
            tool_calls=(), finish_reason="stop",
            usage=TokenUsage(prompt_tokens=15, completion_tokens=10),
            model=request.model, provider="stub_happy",
        )

    async def stream(self, request):
        raise NotImplementedError


async def seed_fixture():
    register_echo_tool()
    # Ensure an `echo` tool row exists with org_id=NULL. The migration seeded
    # three other builtins but not echo (echo is Python-registered only).
    tool_id = uuid7str()
    async with tenant_session(ORG_ID) as s:
        existing = (await s.execute(text(
            "SELECT id FROM tools WHERE key = 'echo' AND org_id IS NULL LIMIT 1"
        ))).scalar_one_or_none()
        if existing:
            tool_id = existing
        else:
            await s.execute(text(
                "INSERT INTO tools "
                "(id, org_id, key, name, description, input_schema, output_schema, "
                " risk_class, approval_policy, timeout_ms, idempotency_mode) "
                "VALUES (:id, NULL, 'echo', 'Echo', 'Echoes input back.', "
                " CAST(:inp AS jsonb), CAST(:out AS jsonb), "
                " 'read_only', 'auto', 15000, 'keyed')"
            ), {
                "id": tool_id,
                "inp": '{"type":"object","properties":{"message":{"type":"string"}},"required":["message"]}',
                "out": '{"type":"object"}',
            })
    async with tenant_session(ORG_ID) as s:
        agent = await create_agent(s, org_id=ORG_ID, name=f"e2e-{__import__('uuid').uuid4().hex[:12]}")
        agent_id = agent.id
        config = AgentConfig(
            instructions="Call the echo tool to complete the task.",
            model="gpt-4o-mini",
            limits=AgentLimits(max_steps=10, max_tool_calls=3,
                               max_seconds=30, max_cost_usd=1.0),
            expected_terminal_tool=None,
            require_verified_terminal=False,
        )
        version = await create_version(s, org_id=ORG_ID, agent_id=agent_id, config=config)
        version_id = version.id
        await grant_tools(s, org_id=ORG_ID, agent_version_id=version_id,
                          grants=[(tool_id, {})])
        await deploy_version(s, org_id=ORG_ID, agent_id=agent_id, agent_version_id=version_id)
    run_id = uuid7str()
    async with tenant_session(ORG_ID) as s:
        await s.execute(text(
            "INSERT INTO agent_runs (id, org_id, agent_version_id, status, input) "
            "VALUES (:id, :o, :v, 'pending', CAST(:inp AS jsonb))"
        ), {"id": run_id, "o": ORG_ID, "v": version_id,
            "inp": '{"task":"use the echo tool to say hello"}'})
    return agent_id, version_id, run_id


async def main() -> int:
    print("ORG_ID = " + ORG_ID)
    agent_id, version_id, run_id = await seed_fixture()
    print("Seeded agent=" + agent_id[:8] + " version=" + version_id[:8] + " run=" + run_id[:8])

    llm = HappyPathLLM(tool_name=TOOL_KEY)
    config = EngineConfig(lease_ttl_seconds=30, heartbeat_interval_seconds=5)
    engine = RuntimeEngine(
        session_factory=get_sessionmaker(), llm=llm, config=config,
        tool_registry=get_tool_registry(),
    )

    print("\nRunning engine.run_once ...")
    terminal = await engine.run_once(run_id=run_id, org_id=ORG_ID)
    print("run_once returned: terminal=" + str(terminal) + " llm.calls=" + str(llm.calls))

    fails = []
    async with tenant_session(ORG_ID) as s:
        row = (await s.execute(text(
            "SELECT status, terminal_reason, finished_at FROM agent_runs WHERE id = :i"
        ), {"i": run_id})).mappings().one_or_none()
        if row is None:
            return _report(["agent_runs row disappeared"])
        if row["status"] != "completed":
            fails.append("agent_runs.status=" + repr(row["status"]) + ", want 'completed'")
        if row["finished_at"] is None:
            fails.append("agent_runs.finished_at is NULL")

        steps = (await s.execute(text(
            "SELECT type FROM execution_steps WHERE execution_id = :i ORDER BY seq"
        ), {"i": run_id})).mappings().all()
        types = [r["type"] for r in steps]
        for required in ("terminated", "tool_result", "tool_proposed"):
            if required not in types:
                fails.append("no step type=" + required + " (got " + str(types) + ")")

        tc = (await s.execute(text(
            "SELECT id, status FROM tool_calls WHERE execution_id = :i"
        ), {"i": run_id})).mappings().all()
        if not tc:
            fails.append("no tool_calls rows")
        elif not any(r["status"] == "completed" for r in tc):
            fails.append("no tool_calls.status='completed'")

        ctx = (await s.execute(text(
            "SELECT messages FROM run_context WHERE execution_id = :i"
        ), {"i": run_id})).scalar_one_or_none()
        if not ctx or len(ctx) < 2:
            fails.append("run_context.messages has " + str(len(ctx) if ctx else 0) + " items")

    return _report(fails)


def _report(fails) -> int:
    if not fails:
        print("\nPASS: end-to-end persisted run completed and verified")
        return 0
    print("\nFAIL:")
    for f in fails:
        print("  - " + f)
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))


