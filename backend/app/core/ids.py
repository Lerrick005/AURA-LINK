"""UUIDv7 generation, time-ordered for index-friendly primary keys.

UUIDv4 is random; its B-tree insert pattern spreads across the whole index.
UUIDv7 embeds a 48-bit millisecond timestamp in the high bits, so inserts
append near the end of the index. That is the difference between clean B-tree
page splits and constant page churn at scale.

We generate client-side rather than using Postgres gen_random_uuid() so the ID
is available before the row is inserted -- the runtime needs the execution id
to seed logs and events before the row exists.

Spec: RFC 9562. Layout:
    48 bits  unix_ts_ms
     4 bits  version (0b0111 = 7)
    12 bits  rand_a
     2 bits  variant (0b10)
    62 bits  rand_b
"""

from __future__ import annotations

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """Generate a UUIDv7 (RFC 9562). Monotonic within a millisecond."""
    ts_ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF  # 48 bits
    rand_a = int.from_bytes(os.urandom(2), "big") & 0x0FFF  # 12 bits
    rand_b = int.from_bytes(os.urandom(8), "big") & 0x3FFFFFFFFFFFFFFF  # 62 bits
    value = (
        (ts_ms << 80)
        | (0x7 << 76)
        | (rand_a << 64)
        | (0b10 << 62)
        | rand_b
    )
    return uuid.UUID(int=value)


def uuid7str() -> str:
    """UUIDv7 as a canonical lowercase string. Used for all new primary keys."""
    return str(uuid7())
