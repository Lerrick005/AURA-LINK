from decimal import Decimal

import pytest

from app.modules.runtime.types import (
    EngineConfig,
    compute_idempotency_key,
    compute_lease_ttl,
)

BASE = dict(execution_id="e1", agent_version_id="v1", tool_id="t1", arguments={"a": 1, "b": 2})


def test_idempotency_key_is_stable_and_argument_order_independent():
    a = compute_idempotency_key(**BASE)
    b = compute_idempotency_key(**{**BASE, "arguments": {"b": 2, "a": 1}})
    assert a == b
    assert a.startswith("ek_") and len(a) == 35


@pytest.mark.parametrize(
    "field,value",
    [
        ("execution_id", "e2"),
        ("agent_version_id", "v2"),
        ("tool_id", "t2"),
        ("arguments", {"a": 1, "b": 3}),
    ],
)
def test_idempotency_key_changes_when_any_input_changes(field, value):
    assert compute_idempotency_key(**BASE) != compute_idempotency_key(**{**BASE, field: value})


def test_engine_config_rejects_heartbeat_too_slow_for_ttl():
    with pytest.raises(ValueError):
        EngineConfig(lease_ttl_seconds=30, heartbeat_interval_seconds=11)
    EngineConfig(lease_ttl_seconds=30, heartbeat_interval_seconds=10)


def test_cost_for_known_and_unknown_model():
    cfg = EngineConfig(lease_ttl_seconds=30, heartbeat_interval_seconds=10)
    assert cfg.cost_for("gpt-4o-mini", 1_000_000, 1_000_000) == Decimal("0.75")
    assert cfg.cost_for("no-such-model", 1_000_000, 1_000_000) == Decimal("0")


def test_lease_ttl_exceeds_tool_timeout():
    assert compute_lease_ttl(15.0) == 45
