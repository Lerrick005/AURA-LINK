"""Append-only audit log. Every authorization decision lands here."""
from app.modules.audit.service import log_action
__all__ = ["log_action"]