"""
SAF-006 — Prompt Injection Detector.

Detect injection in tool outputs / retrieved content. Flags indirect context
poisoning attempts so the agent can ignore or quarantine the input.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_INJECTION_PATTERNS = [
    (re.compile(r"ignore (all )?previous instructions", re.I), "explicit_override"),
    (re.compile(r"\[SYSTEM\][^]]*\]", re.I), "fake_system_block"),
    (re.compile(r"new instructions:", re.I), "instruction_takeover"),
    (re.compile(r"do not (tell|inform) the user", re.I), "deception"),
    (re.compile(r"```(system|assistant)", re.I), "fake_role_block"),
    (re.compile(r"BEGIN_INSTRUCTIONS", re.I), "marker_injection"),
    (re.compile(r"</?(prompt|system)>", re.I), "tag_injection"),
    (re.compile(r"reveal your (prompt|system message)", re.I), "exfil_prompt"),
    # Right-to-left override and related bidi control characters are used to
    # reverse or reorder visible text while keeping the underlying bytes intact,
    # making injected commands invisible or misleading to human reviewers.
    (re.compile(r"[​-‏‪-‮⁦-⁩﻿]"), "bidi_control_chars"),  # nosec B613 — bidi chars are the detection payload, not a TrojanSource attack
]


_HOMOGLYPH_MAP = {
    # Cyrillic small lookalikes
    "\u0430": "a",  # Cyrillic small a
    "\u0432": "B",  # Cyrillic small ve (looks like B or b depending on font, uppercase is B)
    "\u0441": "c",  # Cyrillic small es
    "\u0435": "e",  # Cyrillic small ie
    "\u0456": "i",  # Cyrillic small Byelorussian-Ukrainian i
    "\u0458": "j",  # Cyrillic small je
    "\u043e": "o",  # Cyrillic small o
    "\u0440": "p",  # Cyrillic small er
    "\u0443": "y",  # Cyrillic small u
    "\u0445": "x",  # Cyrillic small ha
    "\u043c": "m",  # Cyrillic small em
    "\u0455": "s",  # Cyrillic small dze
    "\u045e": "u",  # Cyrillic small short u
    "\u043d": "h",  # Cyrillic small en (looks like small capital H)
    "\u0442": "t",  # Cyrillic small te (looks like t/m)
    # Cyrillic capital lookalikes
    "\u0410": "A",
    "\u0412": "B",
    "\u0421": "C",
    "\u0415": "E",
    "\u041d": "H",
    "\u0406": "I",
    "\u0408": "J",
    "\u041a": "K",
    "\u041c": "M",
    "\u041e": "O",
    "\u0420": "P",
    "\u0422": "T",
    "\u0423": "Y",
    "\u0425": "X",
    "\u0405": "S",
    # Greek small lookalikes
    "\u03b1": "a",  # Greek small alpha
    "\u03bf": "o",  # Greek small omicron
    "\u03b5": "e",  # Greek small epsilon
    "\u03b9": "i",  # Greek small iota
    "\u03c5": "y",  # Greek small upsilon
    "\u03c4": "t",  # Greek small tau
    "\u03ba": "k",  # Greek small kappa
    "\u03c1": "p",  # Greek small rho
    "\u03bd": "v",  # Greek small nu
    "\u03c7": "x",  # Greek small chi
    "\u03b7": "n",  # Greek small eta
    # Greek capital lookalikes
    "\u0391": "A",
    "\u0392": "B",
    "\u0395": "E",
    "\u0396": "Z",
    "\u0397": "H",
    "\u0399": "I",
    "\u039a": "K",
    "\u039c": "M",
    "\u039d": "N",
    "\u039f": "O",
    "\u03a1": "P",
    "\u03a4": "T",
    "\u03a5": "Y",
    "\u03a7": "X",
    # Latin Extended lookalikes
    "\u0131": "i",  # Latin small dotless i
    "\u0130": "I",  # Latin capital dotted I
}


def _normalize(text: str) -> str:
    """Return NFKC-normalized text with visual homoglyphs translated to ASCII counterparts."""
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(_HOMOGLYPH_MAP.get(c, c) for c in normalized)


@dataclass
class InjectionFinding:
    pattern: str
    severity: str  # low | medium | high


@dataclass
class InjectionScan:
    findings: list[InjectionFinding]

    @property
    def detected(self) -> bool:
        return any(f.severity in ("medium", "high") for f in self.findings)


def scan_text(text: str) -> InjectionScan:
    findings: list[InjectionFinding] = []
    if not text:
        return InjectionScan(findings)
    # Normalize before matching so Unicode homoglyphs and compatibility
    # characters are mapped to their ASCII equivalents. An attacker who
    # substitutes one or more ASCII characters in a keyword with visually
    # identical Unicode codepoints (e.g. Cyrillic 'o' for Latin 'o') would
    # otherwise bypass every ASCII-only regex pattern silently.
    normalized = _normalize(text)
    for pat, name in _INJECTION_PATTERNS:
        if pat.search(normalized):
            severity = (
                "high"
                if name in ("explicit_override", "fake_system_block", "bidi_control_chars")
                else "medium"
            )
            findings.append(InjectionFinding(pattern=name, severity=severity))
    return InjectionScan(findings)


def quarantine(text: str) -> str:
    """Neutralize a suspicious payload by wrapping it as inert data."""
    return "[QUARANTINED]\n" + re.sub(r"[\n\r]+", " ", text)[:2000]


__all__ = ["InjectionScan", "InjectionFinding", "scan_text", "quarantine"]
