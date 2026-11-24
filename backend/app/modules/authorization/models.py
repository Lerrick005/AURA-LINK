"""Enums for roles, resources, and actions. Python-only, no DB table.

Why enums and not DB rows: the permission matrix is code-reviewed data.
A DB table would make it editable at runtime, which is the opposite of
what we want for a security-critical control. Adding a role is a code
change with a test, not a data change.

Platform Owner is special: it is not a member of any org, it operates
above tenants. It is checked by `is_platform_owner` in policy.py, not
by the matrix.
"""
from __future__ import annotations
from enum import StrEnum


class Role(StrEnum):
    PLATFORM_OWNER = "platform_owner"  # cross-tenant; see policy.py
    ORG_OWNER = "org_owner"
    ORG_ADMIN = "org_admin"
    OPERATOR = "operator"
    SUPERVISOR = "supervisor"
    VIEWER = "viewer"


class Resource(StrEnum):
    ORG = "org"
    USER = "user"
    MEMBERSHIP = "membership"
    AGENT = "agent"
    AGENT_VERSION = "agent_version"
    TOOL = "tool"
    GRANT = "grant"
    RUN = "run"
    EXECUTION_STEP = "execution_step"
    TOOL_CALL = "tool_call"
    ESCALATION = "escalation"
    AUDIT_LOG = "audit_log"
    SESSION = "session"
    API_KEY = "api_key"


class Action(StrEnum):
    READ = "read"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    DEPLOY = "deploy"
    PAUSE = "pause"
    RESUME = "resume"
    APPROVE = "approve"
    RUN = "run"
    CANCEL = "cancel"
    INVITE = "invite"
    ROLE_CHANGE = "role_change"
    REVOKE = "revoke"