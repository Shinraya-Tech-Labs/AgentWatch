"""Dynamic Token and Transformation-Aware Taint Tracker.

Implements the Fides (Microsoft Research / Costa et al.) dynamic information flow model:
Protects against taint-evasion attacks (base64 encoding, hex dumping, URL encoding,
token splitting, and substring interleaving) by dynamically propagating confidentiality
labels across representation transformations.
"""

from __future__ import annotations

import base64
import binascii
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

from panoptis.evidence.redaction import SECRET_PATTERNS
from panoptis.protect.ifc_lattice import SecurityLabel


@dataclass
class TaintRecord:
    source: str
    label: SecurityLabel
    raw_value: str
    variants: set[str] = field(default_factory=set)
    shingles: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        if not self.variants and len(self.raw_value) >= 6:
            # Generate common cryptographic and serialization representations
            raw = self.raw_value
            self.variants.add(raw)
            # Base64 representations
            try:
                b64 = base64.b64encode(raw.encode("utf-8")).decode("utf-8")
                self.variants.add(b64)
                self.variants.add(b64.rstrip("="))
            except (UnicodeError, ValueError):
                pass
            # Hex representations
            try:
                hex_str = raw.encode("utf-8").hex()
                self.variants.add(hex_str)
            except (UnicodeError, ValueError):
                pass
            # URL-encoded representations
            try:
                url_enc = urllib.parse.quote(raw)
                if url_enc != raw:
                    self.variants.add(url_enc)
            except (UnicodeError, ValueError):
                pass

            # Shingles for substring and token-split detection (6-char windows)
            if len(raw) >= 12:
                for i in range(len(raw) - 5):
                    self.shingles.add(raw[i : i + 6])


class DynamicTaintTracker:
    """Session-scoped taint tracker that follows data through encodings and transformations."""

    def __init__(self) -> None:
        self.records: list[TaintRecord] = []

    def register_taint(self, source: str, value: str, label: SecurityLabel) -> None:
        """Register a known confidential or secret value into the dynamic taint register."""
        if not value or len(value) < 4:
            return
        record = TaintRecord(source=source, label=label, raw_value=value)
        self.records.append(record)

    def register_from_payload(self, source: str, payload: Any) -> None:
        """Scan payload for secrets and automatically register their dynamic taints."""
        if not payload:
            return
        text = str(payload)
        for cat, pat in SECRET_PATTERNS:
            for match in pat.finditer(text):
                matched_val = match.group(0)
                self.register_taint(f"{source}:{cat}", matched_val, SecurityLabel.SECRET)

    def evaluate_taint(
        self, value: Any, default_label: SecurityLabel = SecurityLabel.PUBLIC
    ) -> SecurityLabel:
        """Evaluate the effective security label of value by checking transformations and encodings."""
        if value is None:
            return default_label

        text = str(value)
        max_label = default_label

        # 1. Exact or encoded variant matches
        for rec in self.records:
            if rec.raw_value in text:
                max_label = max_label.join(rec.label)
                continue
            for variant in rec.variants:
                if variant in text:
                    max_label = max_label.join(rec.label)
                    break

        # 2. Base64 payload decoding inspection
        # Scan words in text that look like base64
        words = text.split()
        for word in words:
            clean_word = word.strip("\"'()[]{}<>,;:")
            if len(clean_word) >= 16 and len(clean_word) % 4 == 0:
                try:
                    decoded = base64.b64decode(clean_word, validate=True).decode(
                        "utf-8", errors="ignore"
                    )
                    for rec in self.records:
                        if rec.raw_value in decoded:
                            max_label = max_label.join(rec.label)
                except (binascii.Error, ValueError):
                    pass

        # 3. Shingle-based token-split detection (prevents "s" + "k" + "-..." leaks)
        for rec in self.records:
            if rec.shingles and len(text) >= 12:
                matching_shingles = sum(1 for sh in rec.shingles if sh in text)
                # If more than 50% of the secret's shingles appear in the payload, mark as tainted
                if matching_shingles >= max(3, len(rec.shingles) // 2):
                    max_label = max_label.join(rec.label)

        return max_label
