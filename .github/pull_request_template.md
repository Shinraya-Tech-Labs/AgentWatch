## 📌 Description

Provide a clear explanation of what this pull request changes and why.

Fixes # (linked issue number)

---

## 🏛️ Architectural Plane & Classification

Which architectural plane does this change target? ([`docs/v3/V3_ARCHITECTURE.md`](docs/v3/V3_ARCHITECTURE.md))
- [ ] **Plane A: Evidence Plane** (Sensors, RawObservation, Ingest, Redaction)
- [ ] **Plane B: Interpretation Plane** (Normalizers, ComputationalEvent, Entity Resolution)
- [ ] **Plane C: Structure Plane** (Hyperedge Store, Execution/Information/Causal Graphs, Provenance)
- [ ] **Plane D: Model Plane** (Latent States, Motifs, Behavioral Genome, Drift)
- [ ] **Plane E: Experiment Plane** (Replay L0–L4, Timelines, Counterfactuals)
- [ ] **Plane F: Interface Plane** (API, Typer CLI, Next.js Frontend)
- [ ] **Documentation / CI Maintenance**

---

## 🧪 Verification & Evidence of Testing

Please paste the exact command you ran locally and its output:

```bash
# Example: pytest tests/contracts/test_evidence_sufficiency.py
# (Paste output here)
```

- [ ] New unit tests or regression tests were added covering this logic.
- [ ] Existing test suite passes with zero regressions (`pytest tests/`).
- [ ] Test coverage does not decrease (satisfies >= 70% coverage gate).

---

## 🛡️ Security & Linter Quality Checklist

- [ ] **Bandit Scan Passed**: Ran `bandit -r panoptis` with 0 issues found.
- [ ] **No Baseline File**: Confirmed `bandit-baseline.json` was NOT created or edited.
- [ ] **Inline Suppressions (If applicable)**: Any `# nosec` suppression is scoped (`# nosec <ID> # noqa: <RULE>`), includes prose justification, and is documented in [`SECURITY-BASELINE.md`](SECURITY-BASELINE.md).
- [ ] **Ruff Format**: Ran `ruff format --check panoptis/` (Clean pass).
- [ ] **Ruff Check**: Ran `ruff check panoptis/` (Clean pass).
- [ ] **Plane Boundaries**: Verified lower planes do not import from higher planes.

---

## ✍️ Contributor Integrity Declaration

By submitting this pull request, I certify that:
1. I have been formally assigned to this issue by a maintainer.
2. I personally understand and wrote or thoroughly verified every line of this code.
3. This PR contains no unreviewed, hallucinated, or unverified AI-generated slop.
4. My branch is cleanly rebased on the latest `origin/main`.
