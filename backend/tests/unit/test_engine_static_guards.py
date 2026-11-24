"""Static guards for bugs already fixed in engine.py. Cheap, no DB needed."""
from pathlib import Path

ENGINE = Path("app/modules/runtime/engine.py").read_text(encoding="utf-8-sig")


def test_tool_call_never_persisted_with_empty_step_id():
    assert 'step_id=""' not in ENGINE


def test_timeout_on_non_keyed_tool_escalates_indeterminate():
    assert "timed_out and tool.idempotency_mode" in ENGINE


def test_policy_check_validates_arguments_before_proposal():
    assert ENGINE.index("jsonschema.validate") < ENGINE.index('step_type="tool_proposed"')
