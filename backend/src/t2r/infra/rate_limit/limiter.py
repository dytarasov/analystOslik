from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address


def make_limiter() -> Limiter:
    """In-memory limiter keyed by client IP (good enough for MVP)."""
    return Limiter(key_func=get_remote_address)
