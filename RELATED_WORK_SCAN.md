# Related Work Scan: LLMs for Network Configuration & Verification (Post-Oct 2025)

This survey indexes recent literature from October 2025 onward investigating Large and Small Language Models (LLMs/SLMs) applied to network configuration generation, iterative repair, and verifier-guided validation.

---

### 1. Evaluating Agentic Configuration Repair for Computer Networks
- **Authors**: Rufat Asadli, Benjamin Hoffman, Ioannis Protogeros, Laurent Vanbever (ETH Zürich)
- **Date**: June 2026 (arXiv:2606.06212v1)
- **URL**: [https://arxiv.org/abs/2606.06212](https://arxiv.org/abs/2606.06212)
- **Comparison to This Project**: It benchmarks agentic LLM architectures augmented with formal network verification feedback and dynamic context retrieval to iteratively diagnose and repair network misconfigurations (improving efficacy by 12% and safety by 17%); **however**, it evaluates episodic, single-fault repair scenarios in isolation rather than tracking long-horizon policy drift, error compounding, or invariant regression half-life across long sequences (50–200 steps) of ongoing operational edits.

---

### 2. Benchmarking LLM-Driven Network Configuration Repair
- **Authors**: Ioannis Protogeros, Rufat Asadli, Benjamin Hoffman, Laurent Vanbever (ETH Zürich)
- **Date**: April 24, 2026 (arXiv:2604.22513v1)
- **URL**: [https://arxiv.org/abs/2604.22513](https://arxiv.org/abs/2604.22513)
- **Comparison to This Project**: It introduces the *Cornetto* benchmark consisting of 231 synthetically corrupted network configuration scenarios (20 to 754 nodes) evaluated against ground-truth specifications via formal verification, proving that existing LLMs frequently introduce collateral regressions when resolving faults; **however**, it focuses strictly on one-shot repair of pre-corrupted static networks rather than simulating an autonomous assistant executing continuous sequences of new forward intents while maintaining a cumulative ledger of historical invariants.

---

### 3. Network Self-Configuration based on Fine-Tuned Small Language Models
- **Authors**: arXiv:2512.02861v1
- **Date**: December 2, 2025
- **URL**: [https://arxiv.org/abs/2512.02861](https://arxiv.org/abs/2512.02861)
- **Comparison to This Project**: It presents *SLM_netconfig*, a framework that fine-tunes small open models using parameter-efficient adaptation (PEFT/LoRA) and employs an iterative verifier-in-the-loop to translate natural language intents into vendor-compliant configurations with lower latency and higher privacy than commercial cloud LLMs; **however**, its evaluations treat each intent translation as an independent event, without measuring historical invariant survival, cross-step shadowing regressions, or dependency-directed prompt compression over extended deployment lifecycles.

---

### 4. CAP: Detecting Network Device Misconfigurations with Context-Aware Prompting of LLMs
- **Authors**: Aaron Gember-Jacobson et al.
- **Date**: May 2026 (ACM POMACS / SIGMETRICS 2026)
- **URL**: [https://aaron.gember-jacobson.com/docs/pomacs2026cap.pdf](https://aaron.gember-jacobson.com/docs/pomacs2026cap.pdf)
- **Comparison to This Project**: It designs context-aware prompting strategies that retrieve relevant cross-device configuration snippets to enhance LLM accuracy in identifying configuration bugs; **however**, it functions purely as an offline passive detection auditor rather than an active generative agent that synthesizes configuration updates and maintains state across sequential network operations.

---

### 5. NetConfEval: Can LLMs Facilitate Network Configuration?
- **Authors**: ACM CoNEXT 2024 / Proceedings of the ACM on Networking (Published 2024, extended 2025)
- **Date**: ACM CoNEXT 2024 / 2025
- **URL**: [https://dl.acm.org/doi/10.1145/3656296](https://dl.acm.org/doi/10.1145/3656296)
- **Comparison to This Project**: It establishes an extensive benchmark assessing LLMs across diverse standalone network configuration tasks (e.g. BGP peering, OSPF routing, ACL filtering) using syntax checkers and functional reachability tests; **however**, all test cases are executed in complete isolation from clean baseline states, leaving unmeasured the core phenomenon investigated here: how cumulative sequential modifications silently degrade unreferenced historical policies over time.
