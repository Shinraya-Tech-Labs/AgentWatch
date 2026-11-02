# Post-execution output inspection

Every protected tool result is inspected before the agent receives it, and the agent receives the **sanitized** value. Code: `panoptis/protect/output.py` (`OutputInspector`), called by the pipeline after the tool runs. Tests: `tests/protect/test_output_inspection.py`, `tests/protect/test_enforce.py` (streaming), `tests/protect/test_framework_integrations.py` (the value each framework hands back).

## What is detected

| Content | Detected | Action |
|---|---|---|
| Instructions addressed to the model (indirect prompt injection) | override, role-change and concealment phrasing; hidden HTML comments; Unicode tag-block smuggling; bidi controls; invisible characters, alone or in runs; base64 and URL-encoded variants; homoglyphs (NFKC plus a homoglyph map) | **quarantine** (default): only the offending lines are replaced with a marker. **withhold**: the whole result is replaced. **flag**: recorded, not changed. |
| Markdown image whose URL carries data (a zero-click exfiltration beacon) | `![](https://host/?q=…)` | the link is removed |
| Secrets (provider tokens, keys) | the same patterns as argument analysis | masked as `[REDACTED:<kind>]`; the rest of the result is kept |

Benign content is preserved. The lines around an injection are kept, and ordinary text that merely mentions "ignore" or "previous" is untouched (tested).

## Behaviour per result type

| Result | Behaviour |
|---|---|
| `str` | inspected and sanitized |
| `dict`, `list`, `tuple`, `namedtuple`, `set` | walked recursively; the same container type is returned |
| `bytes`, `bytearray` | if valid UTF-8, inspected as text and returned as the same type; clean bytes are returned untouched |
| binary that is not UTF-8 | returned unchanged, with a `BINARY_NOT_INSPECTED` (INFO) finding |
| dataclass, pydantic model, plain object | the fields are inspected, and the object is rebuilt with the same type and sanitized fields (`dataclasses.replace`, `model_copy`, or a copy). If it cannot be rebuilt, the whole result is **withheld**. |
| objects with no fields (slots) | inspected through `str()`; if unsafe, **withheld** |
| `int`, `float`, `bool`, `None` | returned as is |
| generator, async generator (streaming) | every chunk is inspected and sanitized before it is yielded |
| more than `max_scan_chars` (default 2,000,000) | everything past the limit is **withheld**, including the tail of one huge string, with an `UNSCANNED_CONTENT` (HIGH) finding. `on_oversize="pass"` passes it on uninspected instead; this is an explicit opt-in. |
| nested deeper than `max_depth` (default 64) | **withheld** |

Inspecting the full 2,000,000-character budget takes about 1.1 s on the development machine. Lower `max_scan_chars` for latency-sensitive tools.

## Evidence

When a result has findings, a `protect.output` record is written. It carries the finding codes, the paths inside the result, the counts (`redactions`, `quarantined`), whether the result was withheld, and the inspection time.

- **Secrets:** the record holds the count and kind of each secret, never the secret itself (tested).
- **Injections:** the record keeps a short excerpt, so a reviewer can see what was removed.

## Failure behaviour

If inspection itself raises, the result is withheld (`OUTPUT` `STAGE_ERROR`), unless the pipeline is configured with `fail_closed=False`.

## Limitations

- **Stream chunk boundaries:** streamed results are inspected chunk by chunk. An instruction split across two chunks is not seen as one; buffer the stream yourself if a source can split it.
- **Detection is pattern-based, not semantic:** novel phrasing that matches no rule is not detected. The rules are deliberately specific, to keep false positives on ordinary text low.
- **Binary is not inspected:** text inside non-UTF-8 binary (for example UTF-16 without decoding, images, PDFs) is not inspected.
