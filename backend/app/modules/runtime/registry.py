"""Concrete tool registry: maps tool keys to executors and post-checks.

This is the bridge between the engine's protocol (ToolRegistry) and actual
tool implementations. It validates inputs against schemas before execution
and enforces the security boundary.
"""

from __future__ import annotations

import jsonschema
from typing import Any

from app.modules.runtime.types import (
    ToolExecutor,
    PostCheckFn,
    EngineError,
)


class ConcreteToolRegistry:
    """Registry that maps tool keys to executor functions and optional post-checks.
    
    Tools are registered at startup. The engine calls executor_for() and
    post_check_for() during execution. Input validation happens here,
    before any executor runs.
    """

    def __init__(self) -> None:
        self._executors: dict[str, ToolExecutor] = {}
        self._post_checks: dict[str, PostCheckFn] = {}
        self._schemas: dict[str, dict[str, Any]] = {}

    def register(
        self,
        *,
        tool_key: str,
        executor: ToolExecutor,
        input_schema: dict[str, Any] | None = None,
        post_check: PostCheckFn | None = None,
    ) -> None:
        """Register a tool executor and optional post-check.
        
        Args:
            tool_key: Unique identifier for the tool (matches tools.key in DB)
            executor: Async function that executes the tool
            input_schema: JSON Schema for validating tool arguments
            post_check: Optional async function to verify outcomes
        """
        self._executors[tool_key] = executor
        self._schemas[tool_key] = input_schema or {}
        if post_check is not None:
            self._post_checks[tool_key] = post_check

    def executor_for(self, tool_key: str) -> ToolExecutor:
        """Return the executor for a tool key. Raises if not registered."""
        executor = self._executors.get(tool_key)
        if executor is None:
            raise EngineError(f"tool {tool_key!r} not registered")
        return executor

    def post_check_for(self, tool_key: str) -> PostCheckFn | None:
        """Return the post-check for a tool key, or None if not defined."""
        return self._post_checks.get(tool_key)

    def validate_input(self, tool_key: str, arguments: dict[str, Any]) -> None:
        """Validate tool arguments against the registered schema.
        
        Raises EngineError if validation fails. This is called by the engine
        before execution to enforce the security boundary.
        """
        schema = self._schemas.get(tool_key)
        if schema is None:
            return  # No schema = no validation (permissive for MVP)
        
        try:
            jsonschema.validate(instance=arguments, schema=schema)
        except jsonschema.ValidationError as exc:
            raise EngineError(
                f"tool {tool_key!r} input validation failed: {exc.message}"
            ) from exc


# Singleton registry instance (initialized at app startup)
_tool_registry = ConcreteToolRegistry()


def get_tool_registry() -> ConcreteToolRegistry:
    """Return the global tool registry instance."""
    return _tool_registry
