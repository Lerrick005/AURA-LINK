"""Bound run_context.messages growth (F3).

Pure function; no I/O. Called by the engine before every persist of
run_context.messages so a runaway run cannot grow its row without bound.

Eviction order:
  1. Oldest role='tool' messages first (biggest, least needed after read).
  2. Oldest role='user'/'assistant' messages.
Always preserved:
  - The first message if role='system'.
  - The last MAX_MESSAGES_RECENT_KEEP messages.
  - At least one message.
"""
from __future__ import annotations
import json
from typing import Any

from app.modules.runtime.types import (
    MAX_MESSAGES,
    MAX_MESSAGE_TOTAL_BYTES,
    MAX_MESSAGES_RECENT_KEEP,
)


def _size_bytes(msg: Any) -> int:
    try:
        return len(json.dumps(msg, default=str).encode("utf-8"))
    except Exception:
        return len(str(msg).encode("utf-8", errors="replace"))


def trim_messages(messages: list[dict]) -> list[dict]:
    if not messages:
        return messages

    msgs = list(messages)
    has_system = bool(msgs) and msgs[0].get("role") == "system"
    protected_tail = MAX_MESSAGES_RECENT_KEEP
    min_keep = protected_tail + (1 if has_system else 0)

    while True:
        total_bytes = sum(_size_bytes(m) for m in msgs)
        over_count = len(msgs) > MAX_MESSAGES
        over_bytes = total_bytes > MAX_MESSAGE_TOTAL_BYTES
        if not (over_count or over_bytes):
            break

        if len(msgs) <= min_keep:
            # Cannot evict without touching the protected tail. If a single
            # message is huge, truncate it in place rather than evict.
            if over_bytes:
                for i in range(len(msgs) - 1, -1, -1):
                    if i == 0 and has_system:
                        continue
                    s = json.dumps(msgs[i], default=str)
                    if len(s.encode("utf-8")) > 10_000:
                        msgs[i] = {
                            **{k: v for k, v in msgs[i].items() if k != "content"},
                            "content": "<truncated:size_cap>",
                        }
                        break
            break

        start = 1 if has_system else 0
        evict_idx = None
        # Prefer tool messages
        for i in range(start, len(msgs) - protected_tail):
            if msgs[i].get("role") == "tool":
                evict_idx = i
                break
        # Fall back to oldest non-system message
        if evict_idx is None:
            for i in range(start, len(msgs) - protected_tail):
                evict_idx = i
                break
        if evict_idx is None:
            break
        del msgs[evict_idx]

    return msgs
