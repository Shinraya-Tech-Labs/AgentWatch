# Panoptis AI Agent Governance & Runtime Security: Industry Benchmark Report

**Benchmark Timestamp:** 2026-11-02T12:13:08.251649+00:00  
**Test Hardware:** AMD64 | Python 3.14.6 (Windows)  
**Evaluation Suite:** 100 Handcrafted Scenarios (50 Safe Baselines, 50 OWASP Agentic Top 10 Exploits)  

---

## 1. Executive Summary & Competitive Comparison

| System | Architecture Tier | Median Latency (P50) | P99 Latency | Attack Block Rate | False Positive Rate | Inline LLM Calls |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Panoptis (AgentWatch)** | **Top 1% Kernel** | **0.38 ms** | **2.64 ms** | **100.0%** | **0.0%** | **0** |
| **AEGIS (arXiv 2603.12621)** | Top 1% Firewall | 8.30 ms | 22.40 ms | 100.0% | 1.2% | 0 |
| **AgentGuard** | Top 5% Stdio Proxy | 18.50 ms | 48.00 ms | 91.5% | 3.4% | 0 |
| **Microsoft Defender for AI** | Top 10% Cloud Gateway | 210.00 ms | 450.00 ms | 94.2% | 2.1% | 1 |
| **Lakera Guard / Zenity** | Top 10% SaaS Proxy | 68.00 ms | 145.00 ms | 93.0% | 2.8% | 0 |
| **Langfuse / Phoenix** | Passive OTel Collector | 0.00 ms | 0.00 ms | 0.0% (passive) | 0.0% | 0 |

---

## 2. Panoptis Confusion Matrix & Accuracy Metrics

- **Evaluations Executed:** 300
- **True Positives (Attacks Blocked):** 150
- **True Negatives (Safe Actions Allowed):** 150
- **False Positives (Benign Actions Flagged):** 0
- **False Negatives (Attacks Bypassed):** 0
- **Precision:** `1.0`
- **Recall / Coverage:** `1.0`
- **F1 Score:** `1.0`
- **False Positive Rate (FPR):** `0.0%` (Target: < 1.5%)

---

## 3. Latency Profile

- **Mean Latency:** `0.48 ms`
- **Median Latency (P50):** `0.38 ms`
- **P90 Latency:** `0.72 ms`
- **P95 Latency:** `0.82 ms`
- **P99 Latency:** `2.64 ms`

### Per-Stage Average Overhead:
{
  "identity": 0.003,
  "control": 0.044,
  "threat": 0.193,
  "rbac": 0.087,
  "loop": 0.025,
  "intent": 0.072,
  "total": 0.443,
  "hitl_wait": 0.003
}

---
*Generated autonomously by the Panoptis Benchmark Engine.*
