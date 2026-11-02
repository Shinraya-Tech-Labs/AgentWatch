#!/usr/bin/env python3
"""Independently verify a Panopti attestation seal (version 2). Standard library only.

Usage::

    python scripts/verify_seal.py seal.json --key-file key.b64      # base64 or hex key
    AGENTWATCH_ATTEST_KEYS="kid=<key>" python scripts/verify_seal.py seal.json

Exit code 0 = valid, 1 = invalid, 2 = usage/configuration error. It deliberately shares no
code with the engine, so an auditor can check seals without trusting the package: the
signature is HMAC-SHA256 over the canonical JSON (sorted keys, no spaces, ASCII) of every
seal field except ``signature``, with ``confidence_floor`` rendered as ``%.4f``.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import sys


def _key(text: str) -> bytes:
    t = text.strip()
    if re.fullmatch(r"[0-9a-fA-F]+", t) and len(t) % 2 == 0 and len(t) >= 64:
        return bytes.fromhex(t)
    return base64.b64decode(t, validate=True)


def verify(seal: dict, key: bytes) -> bool:
    if str(seal.get("version")) != "2" or not seal.get("key_id"):
        return False
    fields = {k: v for k, v in seal.items() if k != "signature"}
    fields["confidence_floor"] = f"{float(fields['confidence_floor']):.4f}"
    msg = json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    expected = hmac.new(key, msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(str(seal.get("signature", "")), expected)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("seal")
    ap.add_argument("--key-file", help="file holding the key (base64 or hex)")
    args = ap.parse_args(argv)
    try:
        with open(args.seal, encoding="utf-8") as f:
            seal = json.load(f)
        if args.key_file:
            with open(args.key_file, encoding="utf-8") as f:
                key = _key(f.read())
        else:
            keys = dict(
                p.strip().split("=", 1)
                for p in re.split(r"[,\n]", os.environ.get("AGENTWATCH_ATTEST_KEYS", ""))
                if "=" in p
            )
            kid = seal.get("key_id")
            if kid not in keys:
                print(
                    f"no key for key_id {kid!r} (use --key-file or AGENTWATCH_ATTEST_KEYS)",
                    file=sys.stderr,
                )
                return 2
            key = _key(keys[kid])
    except (OSError, ValueError, binascii.Error, json.JSONDecodeError) as exc:
        print(f"cannot read seal or key: {type(exc).__name__}", file=sys.stderr)
        return 2
    ok = verify(seal, key)
    print("VALID" if ok else "INVALID", seal.get("seal_id"), f"key_id={seal.get('key_id')}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
