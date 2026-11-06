# PANOPTIS Governance Benchmark Report

**Benchmark Timestamp:** 2026-11-04T08:21:04.740510+00:00  
**Test Hardware:** AMD64 | Python 3.12.4 (Windows)  
**Evaluation Suite:** 100 project-written synthetic scenarios (50 safe baselines, 50 attacks modelled on the OWASP Agentic Top 10). Not an independent or held-out evaluation.  

---

## 1. Summary

| System | Architecture Tier | Median Latency (P50) | P99 Latency | Attack Block Rate | False Positive Rate | Inline LLM Calls |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Panoptis (AgentWatch)** | **In-process pipeline** | **0.59 ms** | **4.82 ms** | **100.0%** | **0.0%** | **0** |

---

## 2. Panoptis Confusion Matrix & Accuracy Metrics

- **Evaluations Executed:** 100
- **True Positives (Attacks Blocked):** 50
- **True Negatives (Safe Actions Allowed):** 50
- **False Positives (Benign Actions Flagged):** 0
- **False Negatives (Attacks Bypassed):** 0
- **Precision:** `1.0`
- **Recall / Coverage:** `1.0`
- **F1 Score:** `1.0`
- **False Positive Rate (FPR):** `0.0%` (Target: < 1.5%)

---

## 3. Latency Profile

- **Mean Latency:** `0.79 ms`
- **Median Latency (P50):** `0.59 ms`
- **P90 Latency:** `1.23 ms`
- **P95 Latency:** `1.49 ms`
- **P99 Latency:** `4.82 ms`

### Per-Stage Average Overhead:
{
  "identity": 0.004,
  "control": 0.088,
  "threat": 0.329,
  "rbac": 0.135,
  "loop": 0.035,
  "intent": 0.124,
  "total": 0.746,
  "hitl_wait": 0.004
}

---
*Generated autonomously by the Panoptis Benchmark Engine.*
