from __future__ import annotations

import os
import time

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def generate_ulid() -> str:
    """Generate a ULID string (timestamp-based, sortable, 26 chars, Crockford Base32)."""
    timestamp_ms = int(time.time() * 1000)
    random_int = int.from_bytes(os.urandom(10))
    chars: list[str] = []
    for shift in (45, 40, 35, 30, 25, 20, 15, 10, 5, 0):
        chars.append(_CROCKFORD[(timestamp_ms >> shift) & 0x1F])
    for shift in (75, 70, 65, 60, 55, 50, 45, 40, 35, 30, 25, 20, 15, 10, 5, 0):
        chars.append(_CROCKFORD[(random_int >> shift) & 0x1F])
    return "".join(chars)
