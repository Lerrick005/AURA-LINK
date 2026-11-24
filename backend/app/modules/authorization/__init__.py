"""Centralized authorization. Single source of truth for who may do what.

Rules:
- Routes must use `require(action, resource)` from dependencies.py.
- Routes must NOT inspect `actor.roles` directly. A CI meta-test enforces this.
- Every check goes through `authorize()` in policy.py. It returns a Decision,
  never a bare bool, so audit logging cannot be forgotten.
"""
from app.modules.authorization.policy import authorize, Actor, Decision
from app.modules.authorization.models import Role, Resource, Action
from app.modules.authorization.dependencies import require, current_actor

__all__ = [
    "authorize", "Actor", "Decision",
    "Role", "Resource", "Action",
    "require", "current_actor",
]