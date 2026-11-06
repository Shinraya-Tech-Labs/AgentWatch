"""Deterministic Steganography & Covert Channel Neutralizer.

Defeats:
- "Tool Use Enables Undetectable Steganography in Multi-Agent LLM Systems" (arXiv:2606.28425, June 2026)
- Covert data exfiltration via whitespace modulation (trailing spaces/tabs), zero-width character
  encoding, non-deterministic JSON key permutation, and unicode homoglyph substitution.

What it does:
1. Canonical JSON: :meth:`StegoNeutralizer.to_canonical_json_string` is RFC 8785
   (:mod:`panoptis.evidence.jcs`). Neutralizing a payload sorts keys and never changes a number.
2. Trailing whitespace stripping removes line-ending modulation.
3. Unicode NFKC normalization collapses compatibility forms. The pipeline turns this off,
   because it rewrites legitimate text.
4. Invisible character cleansing strips tag blocks and zero-width streams, except joiners that
   shape real text and tags inside a subdivision flag.

This reduces specific channels. It does not eliminate covert channels: text can carry hidden
information in its wording, which no filter of this kind can detect.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from panoptis.evidence import jcs
from panoptis.protect.model import Finding, Severity, Stage, Verdict

# Invisible characters used for hidden binary/ASCII steganography
_INVISIBLE_RE = re.compile(
    r"[\U000E0000-\U000E007F]|[\u202a-\u202e\u2066-\u2069]|[\u200b-\u200f\u2060\ufeff]"
)
# Trailing whitespace on any line (including spaces, tabs, non-breaking spaces)
_TRAILING_WHITESPACE_RE = re.compile(r"[ \t\u00a0\u2000-\u200a]+$", re.MULTILINE)
# A Markdown hard line break: exactly two spaces after visible text
_MARKDOWN_BREAK_RE = re.compile(r"(?<=\S)  $", re.MULTILINE)
_JOINERS = "\u200c\u200d"


def _joins_real_text(text: str, start: int) -> bool:
    """A joiner between two non-ASCII characters shapes text (Persian, Indic, emoji)."""
    return 0 < start < len(text) - 1 and ord(text[start - 1]) > 127 and ord(text[start + 1]) > 127


def _is_flag_tag(text: str, start: int) -> bool:
    """Tag characters are legitimate inside a subdivision flag, which opens with U+1F3F4."""
    i = start
    while i > 0 and 0xE0000 <= ord(text[i - 1]) <= 0xE007F:
        i -= 1
    return i > 0 and text[i - 1] == "\U0001f3f4"


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
        preserve_markdown_breaks: bool = False,
        canonicalize_json: bool = True,
        detect_high_whitespace_entropy: bool = True,
        whitespace_bits_threshold: int = 16,
    ) -> None:
        self.strip_trailing_whitespace = strip_trailing_whitespace
        self.strip_invisible_chars = strip_invisible_chars
        self.normalize_unicode = normalize_unicode
        self.preserve_markdown_breaks = preserve_markdown_breaks
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
            matches = [
                m
                for m in _INVISIBLE_RE.finditer(cleaned)
                if not (m.group() in _JOINERS and _joins_real_text(cleaned, m.start()))
                and not _is_flag_tag(cleaned, m.start())
            ]
            if matches:
                # Each zero-width or tag character can carry 1 to 7 bits of entropy
                bits_neutralized += len(matches) * 4
                for m in reversed(matches):
                    cleaned = cleaned[: m.start()] + cleaned[m.end() :]
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
            trailing_matches = [
                m
                for m in _TRAILING_WHITESPACE_RE.finditer(cleaned)
                if not (
                    self.preserve_markdown_breaks and _MARKDOWN_BREAK_RE.match(cleaned, m.start())
                )
            ]
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
                for m in reversed(trailing_matches):
                    cleaned = cleaned[: m.start()] + cleaned[m.end() :]

        return cleaned, bits_neutralized, findings

    def canonicalize_json_value(self, value: Any) -> Any:
        """``value`` with mapping keys sorted as RFC 8785 sorts them. Values are unchanged."""
        if isinstance(value, Mapping):
            items = sorted(
                value.items(), key=lambda kv: str(kv[0]).encode("utf-16-be", "surrogatepass")
            )
            return {k: self.canonicalize_json_value(v) for k, v in items}
        if isinstance(value, tuple):
            return tuple(self.canonicalize_json_value(item) for item in value)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            return [self.canonicalize_json_value(item) for item in value]
        return value

    def neutralize(self, payload: Any) -> StegoInspectionResult:
        """Recursively inspect and neutralize steganographic channels across arbitrary data types."""
        findings: list[Finding] = []
        total_bits = 0

        def walk(node: Any) -> Any:
            nonlocal total_bits
            if node is None or isinstance(node, (bool, int, float)):
                return node
            if isinstance(node, str):
                cleaned_str, bits, str_findings = self.neutralize_string(node)
                total_bits += bits
                findings.extend(str_findings)
                return cleaned_str
            if isinstance(node, Mapping):
                # Canonical key ordering + recursive walk
                items = (
                    sorted(node.items(), key=lambda kv: str(kv[0]))
                    if self.canonicalize_json
                    else node.items()
                )
                return {k: walk(v) for k, v in items}
            if isinstance(node, tuple):
                return tuple(walk(item) for item in node)
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
        """``obj`` as RFC 8785 canonical JSON. Values it cannot represent exactly are refused."""
        return jcs.dumps(obj)
