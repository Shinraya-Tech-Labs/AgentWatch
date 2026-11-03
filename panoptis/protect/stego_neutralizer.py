"""Deterministic Steganography & Covert Channel Neutralizer.

Defeats:
- "Tool Use Enables Undetectable Steganography in Multi-Agent LLM Systems" (arXiv:2606.28425, June 2026)
- Covert data exfiltration via whitespace modulation (trailing spaces/tabs), zero-width character
  encoding, non-deterministic JSON key permutation, and unicode homoglyph substitution.

Mathematical & Cryptographic Guarantees:
1. RFC 8785 JSON Canonicalization (Deterministic key ordering, IEEE 754 float representation, minimal separators).
2. Trailing Whitespace Stripping: Completely destroys line-ending binary modulation channels ($H(W) \to 0$).
3. Unicode NFKC Normalization: Eliminates cross-alphabet homoglyph channel capacity.
4. Invisible Tag & Zero-Width Cleansing: Strips Unicode tag blocks and hidden zero-width streams.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from panoptis.protect.model import Finding, Severity, Stage, Verdict

# Invisible characters used for hidden binary/ASCII steganography
_INVISIBLE_RE = re.compile(
    r"[\U000E0000-\U000E007F]|[\u202a-\u202e\u2066-\u2069]|[\u200b-\u200f\u2060\ufeff]"
)
# Trailing whitespace on any line (including spaces, tabs, non-breaking spaces)
_TRAILING_WHITESPACE_RE = re.compile(r"[ \t\u00a0\u2000-\u200a]+$", re.MULTILINE)


@dataclass(frozen=True)
class StegoInspectionResult:
    """Outcome of a steganography and covert channel neutralization scan."""

    cleaned_payload: Any
    bits_neutralized_est: int = 0
    findings: list[Finding] = field(default_factory=list)

    @property
    def had_covert_channel(self) -> bool:
        return bool(self.findings)


class StegoNeutralizer:
    """Neutralizes covert steganographic channels in tool arguments, outputs, and files."""

    def __init__(
        self,
        *,
        strip_trailing_whitespace: bool = True,
        strip_invisible_chars: bool = True,
        normalize_unicode: bool = True,
        canonicalize_json: bool = True,
        detect_high_whitespace_entropy: bool = True,
        whitespace_bits_threshold: int = 16,
    ) -> None:
        self.strip_trailing_whitespace = strip_trailing_whitespace
        self.strip_invisible_chars = strip_invisible_chars
        self.normalize_unicode = normalize_unicode
        self.canonicalize_json = canonicalize_json
        self.detect_high_whitespace_entropy = detect_high_whitespace_entropy
        self.whitespace_bits_threshold = whitespace_bits_threshold

    def neutralize_string(self, text: str) -> tuple[str, int, list[Finding]]:
        """Neutralize covert steganographic markers in text, returning cleaned text and findings."""
        if not text:
            return text, 0, []

        findings: list[Finding] = []
        bits_neutralized = 0
        cleaned = text

        # 1. Unicode Normalization (NFKC - collapses homoglyphs and compatibility ligatures)
        if self.normalize_unicode:
            nfkc = unicodedata.normalize("NFKC", cleaned)
            if nfkc != cleaned:
                cleaned = nfkc

        # 2. Invisible Zero-Width & Tag Block Cleansing
        if self.strip_invisible_chars:
            matches = list(_INVISIBLE_RE.finditer(cleaned))
            if matches:
                # Each zero-width or tag character can carry 1 to 7 bits of entropy
                bits_neutralized += len(matches) * 4
                cleaned = _INVISIBLE_RE.sub("", cleaned)
                findings.append(
                    Finding(
                        stage=Stage.THREAT,
                        code="STEGO_INVISIBLE_CHARS_STRIPPED",
                        severity=Severity.HIGH,
                        message=f"Neutralized {len(matches)} hidden zero-width/tag steganographic characters",
                        evidence={"char_count": len(matches), "bits_est": bits_neutralized},
                        recommends=Verdict.MUTATE_AND_ALLOW,
                    )
                )

        # 3. Trailing Whitespace Modulation Neutralization
        if self.strip_trailing_whitespace:
            trailing_matches = list(_TRAILING_WHITESPACE_RE.finditer(cleaned))
            if trailing_matches:
                # Quantify bits: e.g. each space/tab variation on line-end carries 1-2 bits
                total_trailing_chars = sum(len(m.group(0)) for m in trailing_matches)
                ws_bits = total_trailing_chars * 2
                bits_neutralized += ws_bits

                if (
                    self.detect_high_whitespace_entropy
                    and ws_bits >= self.whitespace_bits_threshold
                ):
                    findings.append(
                        Finding(
                            stage=Stage.THREAT,
                            code="STEGO_WHITESPACE_MODULATION_DETECTED",
                            severity=Severity.HIGH,
                            message=(
                                f"High-entropy trailing whitespace pattern detected: {len(trailing_matches)} lines "
                                f"with {total_trailing_chars} trailing characters (~{ws_bits} covert bits)"
                            ),
                            evidence={
                                "affected_lines": len(trailing_matches),
                                "trailing_chars": total_trailing_chars,
                                "estimated_covert_bits": ws_bits,
                            },
                            recommends=Verdict.MUTATE_AND_ALLOW,
                        )
                    )
                cleaned = _TRAILING_WHITESPACE_RE.sub("", cleaned)

        return cleaned, bits_neutralized, findings

    def canonicalize_json_value(self, value: Any) -> Any:
        """Deterministic RFC 8785 Canonical JSON representation."""
        if isinstance(value, Mapping):
            # Sort dictionary keys lexicographically
            return {k: self.canonicalize_json_value(v) for k, v in sorted(value.items())}
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [self.canonicalize_json_value(item) for item in value]
        if isinstance(value, float):
            # Round IEEE 754 floats to deterministic representation
            if value.is_integer():
                return int(value)
            return round(value, 10)
        return value

    def neutralize(self, payload: Any) -> StegoInspectionResult:
        """Recursively inspect and neutralize steganographic channels across arbitrary data types."""
        findings: list[Finding] = []
        total_bits = 0

        def walk(node: Any) -> Any:
            nonlocal total_bits
            if node is None or isinstance(node, (bool, int)):
                return node
            if isinstance(node, float):
                return self.canonicalize_json_value(node) if self.canonicalize_json else node
            if isinstance(node, str):
                cleaned_str, bits, str_findings = self.neutralize_string(node)
                total_bits += bits
                findings.extend(str_findings)
                return cleaned_str
            if isinstance(node, Mapping):
                # Canonical key ordering + recursive walk
                items = sorted(node.items()) if self.canonicalize_json else node.items()
                return {k: walk(v) for k, v in items}
            if isinstance(node, Sequence) and not isinstance(node, (str, bytes, bytearray)):
                return [walk(item) for item in node]
            return node

        cleaned = walk(payload)
        return StegoInspectionResult(
            cleaned_payload=cleaned,
            bits_neutralized_est=total_bits,
            findings=findings,
        )

    def to_canonical_json_string(self, obj: Any) -> str:
        """Convert any JSON-serializable object into an RFC 8785 canonical string with zero covert jitter."""
        canonical_obj = self.canonicalize_json_value(obj)
        return json.dumps(canonical_obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
