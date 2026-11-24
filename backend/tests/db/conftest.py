from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.db import dispose_engine, tenant_session
from app.core.ids import uuid7str
from app.modules.agents.models import Agent, AgentRun, AgentVersion

BACKEND = Path(__file__).resolve().parents[2]

_INSERT_TOOL_CALL = text(
    "INSERT INTO tool_calls (id, org_id, execution_id, step_id, agent_version_id, tool_id, "
    " tool_key_snapshot, risk_class_snapshot, idempotency_mode_snapshot, input, "
    " input_redacted, status, idempotency_key) "
    "VALUES (:id, :o, :e, NULL, :v, :t, 'echo', 'read_only', 'keyed', '{}'::jsonb, true, :st, :ik)"
)


@pytest.fixture(scope="session")
def migrated_db():
    r = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=BACKEND, env=dict(os.environ), capture_output=True, text=True,
    )
    if r.returncode != 0:
        pytest.exit(f"alembic upgrade failed on the TEST database:\n{r.stdout}\n{r.stderr}", returncode=2)


@pytest.fixture
def insert_tool_call():
    async def _insert(s, *, org_id, run_id, version_id, tool_id, status="executing", idem=None):
        call_id = uuid7str()
        await s.execute(
            _INSERT_TOOL_CALL,
            {"id": call_id, "o": org_id, "e": run_id, "v": version_id,
             "t": tool_id, "st": status, "ik": idem or f"k-{call_id}"},
        )
        return call_id
    return _insert


@pytest_asyncio.fixture
async def world(migrated_db, insert_tool_call):
    """Two orgs, each with agent, version, run and one tool_call."""
    made = {}
    for name in ("a", "b"):
        org_id = uuid7str()
        async with tenant_session(org_id) as s:
            agent = Agent(org_id=org_id, name=f"agent-{org_id[:8]}")
            s.add(agent)
            await s.flush()
            ver = AgentVersion(
                agent_id=agent.id, org_id=org_id, version=1,
                config={"instructions": "x", "model": "gpt-4o-mini"},
            )
            s.add(ver)
            await s.flush()
            run = AgentRun(org_id=org_id, agent_version_id=ver.id)
            s.add(run)
            await s.flush()
            tool_id = (await s.execute(text("SELECT id FROM tools ORDER BY key LIMIT 1"))).scalar_one_or_none()
            if tool_id is None:
                pytest.skip("no builtin tool visible to tenants; seed migration did not run?")
            call_id = await insert_tool_call(
                s, org_id=org_id, run_id=run.id, version_id=ver.id, tool_id=tool_id,
            )
        made[name] = SimpleNamespace(
            org_id=org_id, agent_id=agent.id, version_id=ver.id,
            run_id=run.id, tool_id=tool_id, call_id=call_id,
        )
    yield SimpleNamespace(**made)
    await dispose_engine()
