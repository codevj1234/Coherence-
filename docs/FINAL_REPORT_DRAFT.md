# Final Project Report Draft: Coherence Under Change

## Abstract
Network configuration management by Large Language Models (LLMs) offers rapid automation but introduces severe risks of cumulative policy degradation. Over extended operational sequences (50–200 configuration edits), changes that appear locally correct frequently break invariants established earlier in the session. This project introduces **Coherence Under Change**, a rigorous benchmark and harness evaluating sequential policy regression across open- and closed-source models under varying architectural conditions (C0–C3).

---

## 1. Introduction & Related Work Context
Recent studies in LLM-driven network configuration (e.g., *Cornetto*, Protogeros et al., 2026; *Evaluating Agentic Configuration Repair*, Asadli et al., 2026) demonstrate that while models can address isolated faults, they struggle with large-scale interdependencies and often introduce collateral regressions. Unlike prior work focused exclusively on one-shoots or static fault repair, **Coherence Under Change** investigates long-horizon policy drift and measures the **Regression Half-Life** of model-generated edits across continuous operational sequences.

---

## 2. Test-Set & Benchmark Design
Our benchmark generation pipeline is structured strictly for **Test-Set & Benchmark Design**:
- **Locked Test Sequences**: 44 deterministically generated, SHA-256 verified test sequences spanning large (`topo_test_large`) and small (`topo_test_small`) topologies, totaling 2,600 evaluation steps.
- **Strict Train/Test Separation**: Training data (`generator/data/train/`) is isolated with rigorous checks ensuring zero test leakage (zero test sequence IDs, zero test topologies, and zero test-reserved paraphrase n-grams).
- **Trap Checks & Invariants**: Incorporates multi-category trap injections (C01–C12) testing shadowing, prefix overlaps, and VRF isolation leaks.

---

## 3. Experimental Conditions (C0–C3)
We evaluate models across four standardized conditions:
- **C0**: No help (open-loop baseline; errors accumulate).
- **C1**: Checker feedback + up to 3 retries.
- **C2**: Prompt lists only affected rules (dependency-directed prompt filtering).
- **C3**: Full guard (bad edits are rejected and never committed).

---

## 4. Evaluation Results & Model Performance (Placeholders)

### 4.1 Regression Half-Life & Success Rates
*Placeholder for empirical results comparing Qwen2.5-7B-Instruct and Meta-Llama-3.1-8B-Instruct across conditions C0–C3.*

| Model | Condition | Median Regression Half-Life (Steps) | 95% Bootstrap CI | Cumulative Success Rate (%) |
| :--- | :--- | :--- | :--- | :--- |
| **Qwen2.5-7B-Instruct** | C0 (Open-loop) | *[Pending evaluation]* | *[Pending]* | *[Pending]* |
| **Qwen2.5-7B-Instruct** | C1 (Feedback) | *[Pending evaluation]* | *[Pending]* | *[Pending]* |
| **Qwen2.5-7B-Instruct** | C2 (Filtered) | *[Pending evaluation]* | *[Pending]* | *[Pending]* |
| **Qwen2.5-7B-Instruct** | C3 (Guarded) | *[Pending evaluation]* | *[Pending]* | *[Pending]* |
| **Llama-3.1-8B-Instruct** | C0 (Open-loop) | *[Pending evaluation]* | *[Pending]* | *[Pending]* |
| **Llama-3.1-8B-Instruct** | C1 (Feedback) | *[Pending evaluation]* | *[Pending]* | *[Pending]* |
| **Llama-3.1-8B-Instruct** | C2 (Filtered) | *[Pending evaluation]* | *[Pending]* | *[Pending]* |
| **Llama-3.1-8B-Instruct** | C3 (Guarded) | *[Pending evaluation]* | *[Pending]* | *[Pending]* |

---

## 5. Failure Mode Analysis
- **VRF Parse Failures**: Caused by hierarchical syntax errors and omitted global VRF definitions.
- **C3 Guard Rejections**: Systematic rejection of waypoint and prefix-filter edits due to strict pre-commit invariant checks against historical routing policies.
