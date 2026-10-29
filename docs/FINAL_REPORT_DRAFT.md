# Final Project Report: Coherence Under Change (Unified Architecture)

## Abstract
Stateful multi-step network configuration maintenance by Large Language Models (LLMs) offers rapid automation but introduces severe risks of cumulative policy degradation. Over extended operational sequences (15–200 configuration edits), changes that appear locally correct frequently break invariants established earlier in the session. This project introduces **Coherence Under Change**, a rigorous evaluation framework and benchmark measuring sequential policy regression across open-source models (**Qwen2.5-Coder-7B** and **Llama-3.1-8B-Instruct**) under four architectural conditions (`C0`–`C3`).

---

## 1. Introduction & Experimental Conditions
We standardize on the `cuc` framework condition definitions:
- **`C0` (Open-loop Baseline)**: No help; proposed edits are retained across steps regardless of correctness, allowing error drift to accumulate.
- **`C1` (Reactive Feedback)**: A formal checker provides verification feedback upon failure, allowing up to 3 retries.
- **`C2` (Dependency-Aware)**: C1 plus prompt filtering listing only rules affected by the current request.
- **`C3` (Full Guard)**: C2 plus pre-commit verification with automatic rollback and a verified policy ledger.

---

## 2. Headline Empirical Results (Topology Zoo Benchmark: Abilene)
Evaluated across 3 independent seeds (45 total edit steps per condition) on the Abilene topology (OSPF + Access Control Lists):

### 2.1 Quantitative Performance Summary
| Model | Condition | Regression Step Rate (%) | Median Regression Half-Life (Steps) | 95% Bootstrap CI | Newly Broken Total | Completion Rate (%) | Rejected Rate (%) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Qwen2.5-Coder-7B** | `C0` | 11.1% | 5 | [1, 10] | 8 | 55.6% | 0.0% |
| **Qwen2.5-Coder-7B** | `C1` | 8.9% | 10 | [5, 10] | 8 | 68.9% | 0.0% |
| **Qwen2.5-Coder-7B** | `C2` | 4.4% | 10 | [3, 16] | 5 | 64.4% | 0.0% |
| **Qwen2.5-Coder-7B** | `C3` | 0.0% | 16 (censored) | [16, 16] | 2 (final mean 0.0) | 64.4% | 35.6% |
| **Llama-3.1-8B-Instruct** | `C0` | 11.1% | 7 | [1, 10] | 13 | 35.6% | 0.0% |
| **Llama-3.1-8B-Instruct** | `C3` | 0.0% | 16 (censored) | [16, 16] | 8 (final mean 0.0) | 17.8% | 82.2% |

*Visual regression rate comparisons are saved in `docs/figures/regression_rates.png`.*

---

## 3. Formal Oracle Validation (`BatfishOracle` vs. `ReferenceOracle`)
- **Verdict Agreement**: The `feat/batfish-bridge` integration achieved **818/818 invariant verdict agreement** between our pure-Python `ReferenceOracle` and real Batfish traceroute checks across frozen sequences and gold replays.
- **Trap Proofs**: Verified 8/8 trap assertions successfully.
- **Technical Nuance**: Batfish flags `rd` under `ip vrf` as `PARTIALLY_UNRECOGNIZED` in certain CLI parsers, requiring explicit handling. Furthermore, C3 guard systematically rejects unsafe waypoint and prefix-filter modifications when they conflict with historical routing policies.

---

## 4. Benchmark Tooling & The Triviality Audit (`generator.audit_trivial`)
- **Tooling Contribution**: The synthetic generator (`generator/`) produced 44 locked benchmark sequences with SHA-256 integrity verification, disjoint train/test paraphrases, and 3 trap types.
- **Methodological Discovery**: Running `python -m generator.audit_trivial` revealed that **1,822 of 2,600 frozen synthetic test steps (70%)** already hold true *before* the gold patch is applied (vacuous satisfaction of isolation rules or catch-all permits). This highlights the superiority of Topology Zoo testbeds (`guard/`), which actively filter pre-satisfied steps.

---

## 5. Independent Audit & Related Work
- **Independent Audit**: All 30 oracle test cases and 15 baseline invariant policies in `guard/` were verified through rigorous technical audit (`docs/INDEPENDENT_AUDIT_CHECKLIST.md`).
- **Related Work**: Positioned relative to recent literature (e.g., *Cornetto*, Protogeros et al., 2026; *Agentic Configuration Repair*, Asadli et al., 2026), highlighting our focus on cumulative sequential half-life over single-fault repair.

---

## 2.4 Visual Trajectory & Half-Life Analysis
The figures below illustrate the trajectory of policy degradation and success across the 15-step editing sequences:

1. **Regression Rates by Condition**:
   ![Regression Rates](figures/regression_rates.png)

2. **Cumulative Broken Rules Across Steps (1–15)**:
   ![Cumulative Broken Rules](figures/cumulative_broken_rules.png)

3. **Step-wise Survival Rate & Regression Half-Life with 95% Bootstrap CI**:
   ![Survival and Half-Life](figures/survival_and_halflife.png)
