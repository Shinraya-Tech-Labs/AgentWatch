# 02 — EvidenceLab & Temporal Contract Verification

> **Implementation Files:**  
> - [`panoptis/contracts/schema.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/contracts/schema.py) (Data models & 4-valued verdicts)  
> - [`panoptis/contracts/evaluator.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/contracts/evaluator.py) (Temporal contract evaluator & witness extraction)  
> - [`panoptis/contracts/remedies.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/contracts/remedies.py) (Remediation recommendations for missing telemetry)  
> - [`tests/contracts/test_evidence_sufficiency.py`](file:///data/data/com.termux/files/home/AgentWatch/tests/contracts/test_evidence_sufficiency.py) (Contract verification test suite)  
> **Merged PR:** [PR #728](https://github.com/Shinraya-Tech-Labs/AgentWatch/pull/728) (`f87693a`) into `main`  

---

## 💡 The Research Premise of v3.1 EvidenceLab

Standard observability tools (DataDog, Jaeger, OpenTelemetry) operate under a **closed-world assumption**: if an event is absent from the trace, they assume it never happened.

In decentralized and distributed AI agent systems, this assumption causes rampant false alarms:
- A tool was executed, but its specific sensor was disabled in production.
- Network jitter caused a span orphan or clock skew.
- Dynamic sampling dropped intermediate spans.

Under closed-world logic, a safety contract like *"All fund transfers must be preceded by human approval"* will falsely report `REFUTED` simply because the human approval service wasn't wired into the telemetry stream.

**AgentWatch v3.1 EvidenceLab** introduces **Open-World Epistemic Verification**:
> *"AgentWatch doesn't just trace agent decisions. It tells you what your traces prove, what they cannot prove, and what to instrument next."*

---

## ⚖️ Four-Valued Epistemic Logic (`EvidenceVerdict`)

Defined in [`panoptis/contracts/schema.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/contracts/schema.py#L14-L23):

```python
class EvidenceVerdict(StrEnum):
    SUPPORTED = "supported"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
```

1. **`SUPPORTED`**:
   - The trace contains positive, unambiguous computational evidence satisfying the invariant.
   - Example: A `payments.transfer` event is found, and an `approval.granted` event exists within the preceding time window matching the exact `transaction_id`.
2. **`REFUTED`**:
   - The trace contains concrete, witnessed proof that the contract was violated *under verified sensor coverage*.
   - Example: The target event occurred, sensor coverage was active and healthy for the approval channel, yet no approval was granted.
3. **`INCONCLUSIVE`**:
   - The trace lacks sufficient telemetry to verify the invariant.
   - Rather than raising a false security alert, AgentWatch flags an **observational gap** and identifies the uninstrumented subsystem.
4. **`CONFLICTING_EVIDENCE`**:
   - The trace contains contradictory facts due to distributed race conditions, unbounded clock ambiguity intervals, or conflicting span links.

---

## ⏱️ Supported Temporal Operators (`ContractOperator`)

1. **`PRECEDING_EVENT` ($P \mathcal{S} Q$)**:
   - Asserts that whenever target event $Q$ occurs, an instance of event $P$ must have strictly preceded it within a bounded window $T$:
     $$\forall e_q \in Q, \exists e_p \in P \text{ s.t. } 0 < t(e_q) - t(e_p) \le T \land \text{join}(e_q, e_p)$$
2. **`EVENTUALLY_WITHIN` ($\Diamond_{\le T} Q$)**:
   - Asserts that whenever trigger event $P$ occurs, an event $Q$ must follow within window $T$.

---

## 🔍 Missingness Taxonomy (`MissingReason`)

When a contract evaluates to `INCONCLUSIVE`, AgentWatch attaches a typed `MissingFact` explaining the root cause:

| Reason Code | Failure Mode | Resolution Mechanism |
| :--- | :--- | :--- |
| `SENSOR_DISABLED` | Telemetry sensor for the expected event family was not enabled. | Enable sensor profile in AgentWatch config. |
| `FIELD_ABSENT` | The expected event arrived, but join attributes (`transaction_id`) were omitted by the normalizer. | Update normalizer payload extraction rules. |
| `SPAN_ORPHAN` | Event was emitted without parent/trace context. | Instrument context propagation / W3C traceparent. |
| `SEQUENCE_GAP` | Monotonic message sequence counter jumped (missing frame). | Investigate message queue drops or transport loss. |
| `CLOCK_AMBIGUITY` | Clock uncertainty interval $\pm \delta$ exceeds the temporal separation between events. | Synchronize system clocks (PTP/NTP) or use causal links. |

---

## 🩹 Remediation Engine (`panoptis/contracts/remedies.py`)

When an invariant cannot be proven, [`remedies.py`](file:///data/data/com.termux/files/home/AgentWatch/panoptis/contracts/remedies.py) maps missing facts to actionable remediation objects:

- **`remedy_type`**: `CONFIG_CHANGE`, `INSTRUMENTATION_REQUIRED`, or `NETWORK_FIX`.
- **`suggestion`**: Exact human-readable instruction (e.g., *"Enable the 'approval_sensor' in AgentWatch capture profile"*).
- **`documentation_url`**: Direct link to the relevant adapter or sensor integration guide.

---

## 🧪 Verification & PR #728 Details

- **Test Suite**: [`tests/contracts/test_evidence_sufficiency.py`](file:///data/data/com.termux/files/home/AgentWatch/tests/contracts/test_evidence_sufficiency.py) (6/6 passing tests covering witness isolation, missing sensor detection, clock ambiguity handling, and window boundaries).
- **Security Invariants**: Resolved Bandit checks B101 (asserts used for structural validation) and B311 (seeded pseudo-random generators in statistical tests) with strict inline annotations and explanations in [`SECURITY-BASELINE.md`](file:///data/data/com.termux/files/home/AgentWatch/SECURITY-BASELINE.md).
- **Production Merge**: Merged into `main` in commit `f87693a` with all CI workflows passing.
