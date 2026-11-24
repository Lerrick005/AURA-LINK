"""Sample tool: echo tool for testing the execution loop.

This demonstrates the pattern for implementing a tool:
1. Define an async executor function
2. Optionally define a post-check function
3. Register both with the tool registry at startup
"""

from __future__ import annotations

from typing import Any

from app.modules.runtime.types import ToolResult, ResolvedTool


async def echo_executor(
    *,
    tool: ResolvedTool,
    arguments: dict[str, Any],
    idempotency_key: str | None,
    timeout_seconds: float,
) -> ToolResult:
    """Echo tool: returns the input message unchanged.
    
    This is a read-only, idempotent tool with no side effects.
    Used for testing the execution loop and policy checks.
    """
    message = arguments.get("message", "")
    return ToolResult(
        ok=True,
        output={"echo": message, "idempotency_key": idempotency_key},
    )


async def echo_post_check(
    arguments: dict[str, Any],
    output: dict[str, Any],
) -> bool | None:
    """Post-check for echo tool: verify output matches input.
    
    Returns True if verification passes, False if it fails, None if
    verification is not applicable.
    """
    expected = arguments.get("message", "")
    actual = output.get("echo", "")
    return expected == actual


def register_echo_tool() -> None:
    """Register the echo tool with the global registry."""
    from app.modules.runtime.registry import get_tool_registry
    
    registry = get_tool_registry()
    registry.register(
        tool_key="echo",
        executor=echo_executor,
        input_schema={
            "type": "object",
            "properties": {
                "message": {"type": "string"},
            },
            "required": ["message"],
        },
        post_check=echo_post_check,
    )
