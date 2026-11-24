"""F3: run_context.messages must not grow without bound."""
import json
import pytest
from app.modules.runtime.messages import trim_messages
from app.modules.runtime.types import (
    MAX_MESSAGES,
    MAX_MESSAGE_TOTAL_BYTES,
    MAX_MESSAGES_RECENT_KEEP,
)


def _msg(role, content, size=50):
    return {"role": role, "content": "x" * size}


def test_under_cap_is_unchanged():
    msgs = [_msg("system", "sys"), _msg("user", "hi"), _msg("assistant", "yo")]
    out = trim_messages(msgs)
    assert out == msgs


def test_empty_returns_empty():
    assert trim_messages([]) == []


def test_over_count_trims_oldest_tool_first():
    msgs = [{"role": "system", "content": "sys"}] + [
        _msg("tool" if i % 2 == 0 else "assistant", f"m{i}")
        for i in range(150)
    ]
    out = trim_messages(msgs)
    assert len(out) <= MAX_MESSAGES
    assert out[0]["role"] == "system"
    # Last MAX_MESSAGES_RECENT_KEEP are preserved
    assert out[-MAX_MESSAGES_RECENT_KEEP:] == msgs[-MAX_MESSAGES_RECENT_KEEP:]


def test_over_bytes_trims_to_cap():
    huge = {"role": "tool", "content": "x" * 100_000}
    msgs = [{"role": "system", "content": "sys"}] + [huge] * 20
    out = trim_messages(msgs)
    total = sum(len(json.dumps(m, default=str).encode("utf-8")) for m in out)
    assert total <= MAX_MESSAGE_TOTAL_BYTES or len(out) <= MAX_MESSAGES_RECENT_KEEP + 1


def test_system_prompt_never_dropped():
    msgs = [{"role": "system", "content": "sys"}] + [_msg("tool", f"t{i}") for i in range(500)]
    out = trim_messages(msgs)
    assert out[0] == {"role": "system", "content": "sys"}


def test_no_system_prompt_still_works():
    msgs = [_msg("user", f"u{i}") for i in range(200)]
    out = trim_messages(msgs)
    assert len(out) <= MAX_MESSAGES
