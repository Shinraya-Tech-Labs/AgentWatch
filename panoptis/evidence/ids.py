"""Monotonic ULID generation (stdlib only)."""

from __future__ import annotations

import os
import threading
import time

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_lock = threading.Lock()
_last_ms = -1
_last_rand = 0


def _encode(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_ALPHABET[value & 31])
        value >>= 5
    return "".join(reversed(out))


def new_ulid(now_ms: int | None = None) -> str:
    """Return a 26-char ULID. IDs generated in one process are strictly increasing."""
    global _last_ms, _last_rand
    with _lock:
        ms = int(time.time() * 1000) if now_ms is None else now_ms
        if ms <= _last_ms:
            ms = _last_ms
            rand = _last_rand + 1
        else:
            rand = int.from_bytes(os.urandom(10), "big") >> 1  # leave headroom for increments
        _last_ms, _last_rand = ms, rand
    return _encode(ms, 10) + _encode(rand & ((1 << 80) - 1), 16)


def ulid_timestamp_ms(ulid: str) -> int:
    value = 0
    for ch in ulid[:10]:
        value = (value << 5) | _ALPHABET.index(ch)
    return value
