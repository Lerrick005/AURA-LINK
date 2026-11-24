import pytest

from app.modules.runtime.registry import ConcreteToolRegistry
from app.modules.runtime.types import EngineError
from app.modules.tools.echo import echo_executor, echo_post_check

SCHEMA = {
    "type": "object",
    "properties": {"message": {"type": "string"}},
    "required": ["message"],
}


def make_registry() -> ConcreteToolRegistry:
    reg = ConcreteToolRegistry()
    reg.register(
        tool_key="echo", executor=echo_executor,
        input_schema=SCHEMA, post_check=echo_post_check,
    )
    return reg


def test_unregistered_tool_raises():
    with pytest.raises(EngineError):
        make_registry().executor_for("nope")


def test_post_check_lookup():
    reg = make_registry()
    assert reg.post_check_for("echo") is echo_post_check
    assert reg.post_check_for("nope") is None


def test_validate_input_accepts_valid_arguments():
    make_registry().validate_input("echo", {"message": "hi"})


@pytest.mark.parametrize("args", [{}, {"message": 5}, {"message": None}])
def test_validate_input_rejects_bad_arguments(args):
    with pytest.raises(EngineError):
        make_registry().validate_input("echo", args)


async def test_echo_executor_echoes_and_passes_idempotency_key():
    res = await echo_executor(
        tool=None, arguments={"message": "hello"},
        idempotency_key="ek_x", timeout_seconds=1.0,
    )
    assert res.ok is True
    assert res.output == {"echo": "hello", "idempotency_key": "ek_x"}


async def test_echo_post_check_true_and_false():
    assert await echo_post_check({"message": "a"}, {"echo": "a"}) is True
    assert await echo_post_check({"message": "a"}, {"echo": "b"}) is False
