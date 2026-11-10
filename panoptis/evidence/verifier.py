"""Independent verifier for exported PANOPTIS evidence (TO-026).

Standard library only, and no PANOPTIS imports: copy this one file anywhere and run

    python verifier.py bundle.json [--expect-head SEQ:SEGMENT_HASH]
    python verifier.py --proof proof.json

A bundle (``panoptis protect evidence export``) holds a tenant's sealed segments and
their observations exactly as stored. The verifier recomputes everything itself:

* every payload's SHA-256 against the recorded ``payload_sha256``;
* every leaf (version 1: id, payload hash, idempotency key, tenant; version 2: the whole
  envelope), every segment's Merkle root, observation count, first/last ids and hash;
* the hash chain from the genesis value through every segment, without gaps;
* optionally, that a head known from elsewhere (an earlier export, a signed audit token)
  is still part of the chain, so a chain cut back or rewritten below it is rejected.

A proof file (``panoptis protect evidence proof``) is checked the same way for one record:
its leaf from the envelope, the path to the Merkle root, and the segment hash.

Exit status 0 means valid; 1 means tampered, incomplete or malformed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from typing import Any

GENESIS = "0" * 64
FORMAT = "agentwatch.evidence.bundle/1"
ENVELOPE_FIELDS = (
    "obs_id",
    "tenant_id",
    "sensor_type",
    "sensor_version",
    "sensor_instance",
    "source_kind",
    "source_seq",
    "idempotency_key",
    "observed_at",
    "received_at",
    "clock",
    "content_type",
    "payload_sha256",
    "declared_ids",
    "redaction",
    "sampling",
)


def _h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def leaf(obs: dict[str, Any], version: int) -> str:
    if version == 1:
        return _h(
            f"leaf|{obs['obs_id']}|{obs['payload_sha256']}|{obs['idempotency_key']}|{obs['tenant_id']}"
        )
    env = {k: obs.get(k) for k in ENVELOPE_FIELDS}
    return _h("leaf2|" + json.dumps(env, sort_keys=True, separators=(",", ":"), ensure_ascii=False))


def merkle_root(leaves: list[str]) -> str:
    if not leaves:
        return _h("empty")
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [_h(f"node|{level[i]}|{level[i + 1]}") for i in range(0, len(level), 2)]
    return level[0]


def segment_hash(tenant: str, seq: int, root: str, prev: str, n: int, version: int) -> str:
    tag = "segment" if version == 1 else f"segment{version}"
    return _h(f"{tag}|{tenant}|{seq}|{root}|{prev}|{n}")


def verify_bundle(
    bundle: dict[str, Any], expected_head: dict[str, Any] | None = None
) -> dict[str, Any]:
    errors: list[str] = []
    if not isinstance(bundle, dict) or bundle.get("format") != FORMAT:
        return {"ok": False, "errors": [f"not a PANOPTIS evidence bundle ({FORMAT})"]}
    tenant = bundle.get("tenant_id")
    segments = bundle.get("segments") or []
    observations = bundle.get("observations") or []
    by_segment: dict[str, list[dict[str, Any]]] = {}
    for o in observations:
        by_segment.setdefault(str(o.get("segment_id")), []).append(o)
    prev = GENESIS
    checked = 0
    for i, seg in enumerate(segments, 1):
        sid = seg.get("segment_id")
        version = int(seg.get("leaf_version") or 1)
        if seg.get("seq") != i:
            errors.append(f"segment {sid}: sequence gap (expected seq {i})")
        if seg.get("tenant_id") != tenant:
            errors.append(f"segment {sid}: tenant mismatch")
        if seg.get("prev_segment_hash") != prev:
            errors.append(f"segment {sid}: chain broken")
        expect = segment_hash(
            str(tenant),
            int(seg.get("seq") or 0),
            str(seg.get("merkle_root")),
            str(seg.get("prev_segment_hash")),
            int(seg.get("n_obs") or 0),
            version,
        )
        if expect != seg.get("segment_hash"):
            errors.append(f"segment {sid}: segment hash mismatch")
        prev = seg.get("segment_hash")
        if seg.get("purged_at"):
            by_segment.pop(str(sid), None)
            continue
        rows = sorted(by_segment.pop(str(sid), []), key=lambda o: o.get("obs_id", ""))
        checked += len(rows)
        if len(rows) != seg.get("n_obs"):
            errors.append(
                f"segment {sid}: expected {seg.get('n_obs')} observations, found {len(rows)}"
            )
        if rows and (
            rows[0].get("obs_id") != seg.get("first_obs")
            or rows[-1].get("obs_id") != seg.get("last_obs")
        ):
            errors.append(f"segment {sid}: first/last observation mismatch")
        for o in rows:
            if o.get("tenant_id") != tenant:
                errors.append(f"observation {o.get('obs_id')}: tenant mismatch")
            body = o.get("payload")
            if not isinstance(body, str) or _h(body) != o.get("payload_sha256"):
                errors.append(f"observation {o.get('obs_id')}: payload hash mismatch")
        if merkle_root([leaf(o, version) for o in rows]) != seg.get("merkle_root"):
            errors.append(f"segment {sid}: merkle root mismatch")
    for sid, rows in by_segment.items():
        errors.append(
            f"{len(rows)} observation(s) belong to segment {sid}, which is not in the chain"
        )
    head = (
        {"seq": segments[-1]["seq"], "segment_hash": segments[-1]["segment_hash"]}
        if segments
        else None
    )
    if head != bundle.get("head"):
        errors.append("the bundle's declared head is not the last segment")
    if expected_head:
        have = next((s for s in segments if s.get("seq") == int(expected_head["seq"])), None)
        if have is None:
            errors.append(
                f"chain truncated: expected head segment {expected_head['seq']} is missing"
            )
        elif have.get("segment_hash") != expected_head.get("segment_hash"):
            errors.append(
                f"chain rewritten: segment {expected_head['seq']} differs from the expected head"
            )
    return {
        "ok": not errors,
        "tenant_id": tenant,
        "segments": len(segments),
        "observations": checked,
        "head": head,
        "errors": errors,
    }


def verify_proof(proof: dict[str, Any]) -> dict[str, Any]:
    """One record: leaf from its envelope, path to the Merkle root, and the segment hash."""
    errors: list[str] = []
    version = int(proof.get("leaf_version") or 1)
    env = proof.get("envelope")
    leaf_value = proof.get("leaf")
    if version >= 2:
        if not isinstance(env, dict):
            return {"ok": False, "errors": ["a version-2 proof needs the record envelope"]}
        if env.get("obs_id") != proof.get("obs_id"):
            errors.append("the envelope is not the proved record")
        leaf_value = leaf(env, version)
        if leaf_value != proof.get("leaf"):
            errors.append("the record does not hash to the proved leaf")
    acc = str(leaf_value)
    for sibling, side in proof.get("proof") or []:
        acc = _h(f"node|{sibling}|{acc}") if side == "L" else _h(f"node|{acc}|{sibling}")
    if acc != proof.get("merkle_root"):
        errors.append("the inclusion path does not lead to the segment's Merkle root")
    seg = proof.get("segment") or {}
    if seg:
        expect = segment_hash(
            str(seg.get("tenant_id")),
            int(seg.get("seq") or 0),
            str(proof.get("merkle_root")),
            str(seg.get("prev_segment_hash")),
            int(seg.get("n_obs") or 0),
            version,
        )
        if expect != proof.get("segment_hash"):
            errors.append("segment hash mismatch")
    return {
        "ok": not errors,
        "obs_id": proof.get("obs_id"),
        "segment_id": proof.get("segment_id"),
        "errors": errors,
    }


def _head(arg: str | None) -> dict[str, Any] | None:
    if not arg:
        return None
    seq, _, digest = arg.partition(":")
    return {"seq": int(seq), "segment_hash": digest}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Verify exported PANOPTIS evidence")
    ap.add_argument("bundle", nargs="?", help="bundle JSON file")
    ap.add_argument("--expect-head", help="SEQ:SEGMENT_HASH of a head known from elsewhere")
    ap.add_argument("--proof", help="verify one record's inclusion proof file instead")
    ns = ap.parse_args(argv)
    try:
        if ns.proof:
            with open(ns.proof, encoding="utf-8") as f:
                report = verify_proof(json.load(f))
        elif ns.bundle:
            with open(ns.bundle, encoding="utf-8") as f:
                report = verify_bundle(json.load(f), _head(ns.expect_head))
        else:
            ap.error("give a bundle file or --proof")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report = {"ok": False, "errors": [f"unreadable input: {exc}"]}
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
