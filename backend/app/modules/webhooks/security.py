"""Webhook signature and timestamp verification.

Format follows GitHub/Stripe convention:
  X-Auralink-Signature-256: sha256=<hex>
  X-Auralink-Timestamp:     <unix seconds as string>

Replay protection: reject requests whose timestamp is more than
TOLERANCE_SECONDS from server time. Callers with clock skew beyond that
must retry with a corrected timestamp.
"""
from __future__ import annotations

import hashlib
import hmac
import time


TOLERANCE_SECONDS = 300


def verify_signature(*, secret: str, body: bytes, header: str) -> bool:
    """Constant-time HMAC-SHA256 comparison against the header."""
    if not header.startswith("sha256="):
        return False
    expected = hmac.new(
        secret.encode("utf-8"), body, hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(f"sha256={expected}", header)


def verify_timestamp(header: str) -> bool:
    """Reject timestamps outside +/- TOLERANCE_SECONDS of now."""
    try:
        ts = int(header)
    except (ValueError, TypeError):
        return False
    return abs(time.time() - ts) <= TOLERANCE_SECONDS


__all__ = ["verify_signature", "verify_timestamp", "TOLERANCE_SECONDS"]
