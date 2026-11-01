"""Canonical JSON encoding and hashing.

Canonical form: UTF-8, sorted keys, no insignificant whitespace, non-finite floats
rejected. Two semantically equal JSON values always hash identically.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from typing import Any
from uuid import UUID


def _default(obj: Any) -> Any:
    if isinstance(obj, datetime | date):
        return obj.isoformat()
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, bytes):
        return {"$bytes_sha256": hashlib.sha256(obj).hexdigest(), "$len": len(obj)}
    if isinstance(obj, set | frozenset | tuple):
        return list(obj)
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json")
    return repr(obj)


def _check_finite(obj: Any) -> None:
    if isinstance(obj, float) and not math.isfinite(obj):
        raise ValueError("non-finite float is not representable in canonical JSON")
    if isinstance(obj, dict):
        for v in obj.values():
            _check_finite(v)
    elif isinstance(obj, list | tuple):
        for v in obj:
            _check_finite(v)


def to_jsonable(obj: Any) -> Any:
    """Round-trip through canonical JSON to obtain plain JSON types."""
    return json.loads(canonical_json(obj))


def canonical_json(obj: Any) -> str:
    """Encode ``obj`` as canonical JSON text."""
    _check_finite(obj)
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=_default,
        allow_nan=False,
    )


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()
