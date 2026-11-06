"""JSON Canonicalization Scheme (RFC 8785).

Object keys are sorted by their UTF-16 code units, strings use the minimal JSON escapes,
numbers use the ECMAScript ``Number`` to string conversion and there is no insignificant
whitespace. Input outside the I-JSON domain the RFC assumes is rejected rather than
altered: non-finite numbers, integers beyond 2**53, lone surrogates and non-string keys.

This is not the encoding the evidence store hashes (see :mod:`panoptis.evidence.canonical`).
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

_MAX_SAFE_INTEGER = 2**53 - 1


def _number(value: float) -> str:
    """ECMAScript ``Number::toString``, from the shortest round-trip digits of the double."""
    if not math.isfinite(value):
        raise ValueError(f"RFC 8785 has no representation for {value!r}")
    if value == 0:
        return "0"
    sign = "-" if value < 0 else ""
    mantissa, _, exponent = repr(abs(value)).partition("e")
    whole, _, fraction = mantissa.partition(".")
    raw = whole + fraction
    digits = raw.lstrip("0")
    point = (
        len(whole) + int(exponent or 0) - (len(raw) - len(digits))
    )  # value = 0.digits * 10**point
    digits = digits.rstrip("0")
    count = len(digits)
    if count <= point <= 21:
        return f"{sign}{digits}{'0' * (point - count)}"
    if 0 < point <= 21:
        return f"{sign}{digits[:point]}.{digits[point:]}"
    if -6 < point <= 0:
        return f"{sign}0.{'0' * -point}{digits}"
    power = point - 1
    fraction = f".{digits[1:]}" if count > 1 else ""
    return f"{sign}{digits[0]}{fraction}e{'+' if power >= 0 else '-'}{abs(power)}"


def _string(value: str) -> str:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise ValueError("RFC 8785 strings must be valid Unicode") from exc
    return json.dumps(value, ensure_ascii=False)


def _utf16_key(key: str) -> bytes:
    return key.encode("utf-16-be", "surrogatepass")


def dumps(value: Any) -> str:
    """Serialize ``value`` to its RFC 8785 canonical text."""
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, int):
        if abs(value) > _MAX_SAFE_INTEGER:
            raise ValueError(f"{value} is outside the integers RFC 8785 can represent exactly")
        return str(value)
    if isinstance(value, float):
        return _number(value)
    if isinstance(value, Mapping):
        if not all(isinstance(k, str) for k in value):
            raise TypeError("RFC 8785 object keys must be strings")
        items = sorted(value.items(), key=lambda kv: _utf16_key(kv[0]))
        return "{" + ",".join(f"{_string(k)}:{dumps(v)}" for k, v in items) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(dumps(v) for v in value) + "]"
    raise TypeError(f"RFC 8785 cannot represent {type(value).__name__}")
