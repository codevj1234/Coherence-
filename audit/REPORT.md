# Independent Audit Report: Phase 2 Change-Sequence Generator

**Auditor Role**: Independent Senior Auditor  
**Audit Target**: Phase 2 Change-Sequence Generator & Frozen Evaluation Benchmark  
**Date**: October 15, 2026  
**Repository Working Directory**: `D:\Computer_networks_proj`  

---

## 1. Audit Summary Matrix

| # | Check | Status | Key Evidence / Metric |
| :---: | :--- | :---: | :--- |
| **1** | **Baseline & Agent Directory Integrity** | **PASS** | 114 passed in `pytest -q`. All 19 Python files and schemas in `agent/` are byte-for-byte identical to the pre-Phase-2 archive. |
| **2** | **Topologies & Baseline Configurations** | **PASS** | 4 topologies (sizes 6, 10, 25, 50) with 20 baseline invariants each. Zero device or subnet overlap between train and test. All configs round-trip with 0 warnings/errors. |
| **3** | **Independent Oracle Check** | **FAIL** | 100% agreement on 80 baseline invariants. 0 baseline regressions on replay. However, replay checker failed to accumulate step invariants, and step invariants from current generator mismatch frozen files. |
| **4** | **Traps & Contradictions** | **FAIL** | All 40 sequences have exactly 15/50 traps (30.0%), but **0 isolation_leak traps exist** (568 prefix_overlap, 32 shadowing, 0 isolation_leak). 0 intent contradictions found. |
| **5** | **Prompt-Safe Data Leakage** | **PASS** | 40/40 prompt-safe files audited: 0 non-null `gold_patch`, 0 leaked config lines, 0 keyword hits (`gold`, `trap`, `naive`, `regress`), 0 leaked trap metadata keys. |
| **6** | **Paraphrase Hold-Out & Disjointness** | **PASS** | Train and test paraphrase pools are strictly disjoint across all 5 intent types. All 11 reserved test phrases have 0 occurrences in training data. |
| **7** | **Frozen Data & Determinism** | **FAIL** | Generator is deterministic across `PYTHONHASHSEED` (0 vs 123), and `FROZEN.json` matches disk files. However, regenerating with current code mismatches frozen files across **40/40** sequences. |
| **8** | **Corruption Suite Quality** | **FAIL** | Existing corruption suite implements only 4 synthetic modes repeated ~13 times each. In 10 new nuanced corruptions, detection rate was 7/10 (70.0%). |
| **9** | **Sequence Shape & Contract Integrity** | **PASS** | All 40 sequences have length 50. 2,000/2,000 Intent records strictly validate against `agent.contract.models.Intent` (`extra="forbid"`). Generator supports lengths 50–200. |

---

## 2. Detailed Findings by Check

### Check 1: Baseline & Agent Directory Integrity
- **Test Baseline**: Running `python -m pytest -q` from root passed **114 tests** in 1.98s (86 pre-existing agent tests + 28 new Phase 2 generator tests).
```
........................................................................ [ 63%]
..........................................                               [100%]
114 passed in 1.98s
```
- **Git Status**: Project root is not a Git repository (`fatal: not a git repository (or any of the parent directories): .git`).
- **Archive Audit**: Audited against pre-Phase-2 archive `D:\Computer_networks_proj.zip`. Every single file under `agent/` (19 Python source files and JSON schemas) matches byte-for-byte with identical SHA-256 hashes. The only addition was `agent/.pytest_cache/`.

### Check 2: Topologies & Baseline Configurations
- **Topologies Inventory**:
  - `topo_train_small`: 10 nodes, 9 links, 2 VRFs (`BLUE`, `RED`), 20 baseline invariants, split: `train`.
  - `topo_train_medium`: 25 nodes, 24 links, 2 VRFs (`BLUE`, `RED`), 20 baseline invariants, split: `train`.
  - `topo_test_small`: 6 nodes, 5 links, 2 VRFs (`CORP`, `GUEST`), 20 baseline invariants, split: `test`.
  - `topo_test_large`: 50 nodes, 49 links, 2 VRFs (`CORP`, `GUEST`), 20 baseline invariants, split: `test`.
- **Target Sizes**: 10, 25, 50, and 6 (small) met.
- **Disjointness**: Device name overlap is `set()`; Subnet CIDR overlap is `set()`. Zero overlap between train and test.
- **Round-Trip Parser/Resolver**: All baseline device configurations round-trip through `parse_model_output` and `resolve_devices` with 0 warnings and 0 errors.

### Check 3: Independent Oracle Check
- **Implementation**: Created an independent packet-tracing checker in [audit/independent_checker.py](file:///D:/Computer_networks_proj/audit/independent_checker.py) implementing longest-prefix-match static routing, VRF boundary isolation, extended ACL first-match with port/protocol wildcards, implicit deny, and waypoint path validation without importing `generator.oracle.reference`.
- **Baseline Invariant Agreement**: 80/80 baseline invariants evaluated across all 4 topologies agreed 100% with `ReferenceOracle`.
- **Replay Execution**: Replaying all 40 frozen sequences against baseline invariants resulted in **0 baseline regressions** across 2,000 steps.
- **Batfish Status**: `pybatfish` is not installed (`ModuleNotFoundError: No module named 'pybatfish'`). Batfish oracle was cleanly skipped.
- **Flaws Found**: In [generator/replay_check.py](file:///D:/Computer_networks_proj/generator/replay_check.py#L37-L68), `replay_single_sequence` only checks `spec.baseline_invariants` and never appends the new step invariants to `cumulative_invariants` during replay. Furthermore, the sidecar manifest only stores invariant IDs (strings) rather than serialized `Invariant` models.

### Check 4: Traps & Contradictions
- **Trap Ratio**: Every single test sequence contains exactly 15/50 traps (**30.0%**), satisfying the 25%–35% requirement.
- **Trap Kind Imbalance**:
  - `prefix_overlap`: 568 traps (94.7%)
  - `shadowing`: 32 traps (5.3%)
  - `isolation_leak`: **0 traps (0.0%)**
- **Discrepancy**: Zero `isolation_leak` traps exist in the frozen test set due to a syntax bug in `generator/traps/isolation_leak.py` present during initial generation.
- **Contradictions**: 0 pairwise intent contradictions across all 2,000 steps in all 40 sequences.
- **Proof Verification**: Evaluated sample shadowing and prefix overlap traps under [audit/verify_trap_diffs.py](file:///D:/Computer_networks_proj/audit/verify_trap_diffs.py). The naive edit broke the specified invariant while the gold patch preserved all invariants.

### Check 5: Prompt-Safe Data Leakage
- **Files Audited**: All 40 `*.prompt_safe.jsonl` files (2,000 intent records).
- **Gold Patch Check**: `gold_patch` key is `null` in all 2,000 records (0 non-null values).
- **Config Line Search**: Searched 1,289 unique configuration lines extracted from full gold patches; 0 leaked into prompt text.
- **Keyword Grep**: 0 occurrences of `"gold"`, `"trap"`, `"naive"`, or `"regress"` inside intent text.
- **Sidecar Metadata**: 0 occurrences of trap metadata keys (`naive_description`, `broken_invariant_id`, `trap_kind`, `shadowing`, `prefix_overlap`, `isolation_leak`) in any prompt-safe file.

### Check 6: Paraphrase Hold-Out & Disjointness
- **Paraphrase Pools**: Evaluated train and test templates across all 5 intent types. Zero template overlap.
- **Reserved Phrases**: 11 phrases reserved exclusively for test splits:
  `['targeting port', 'directed to', 'establishing', 'prohibiting', 'virtual routing domain', 'Guarantee that', 'Suppress the advertisement', 'prevent route installation', 'Mandate that', 'steer through', 'inspected at node']`
- **Training Set Audit**: 0 reserved test phrases appear in any of the 20 sequence files in `generator/data/train/`.

### Check 7: Frozen Data & Determinism
- **Hash Integrity**: `generator.verify_frozen` verified all 40 test sequences against [generator/data/FROZEN.json](file:///D:/Computer_networks_proj/generator/data/FROZEN.json) byte-for-byte.
- **PRNG Determinism**: The generator produces identical SHA-256 hashes under `PYTHONHASHSEED=0` and `PYTHONHASHSEED=123` (0 mismatches).
- **Regeneration Discrepancy**: When regenerating test sequences using the current codebase, **40 out of 40 files differ** from the frozen benchmark files.
  - *Root Cause*: Router baseline ACL rules were changed in [generator/topology/generator.py](file:///D:/Computer_networks_proj/generator/topology/generator.py#L88-L109) after the bulk dataset was created, and `generator.build --bulk-frozen` was never re-run.

### Check 8: Corruption Suite Quality
- **Existing Suite**: Claims 5 modes in comments, but implements only 4 modes: `["malformed_fence", "delete_device", "drop_deny", "widen_prefix"]`. These 4 modes are repeated 12–13 times across 50 iterations.
- **New Corruptions Test**: 10 new nuanced corruptions tested in [audit/check8_corruptions.py](file:///D:/Computer_networks_proj/audit/check8_corruptions.py):
  - `C01_acl_line_reorder_shadowing`: DETECTED (Oracle)
  - `C02_wrong_wildcard_mask`: MISSED (wildcard did not match probe IPs)
  - `C03_wrong_route_next_hop`: DETECTED (Oracle)
  - `C04_vrf_forwarding_swap`: DETECTED (Oracle)
  - `C05_omitted_access_group_in`: DETECTED (Oracle)
  - `C06_corrupted_interface_netmask`: DETECTED (Oracle)
  - `C07_action_permit_deny_swap`: DETECTED (Oracle)
  - `C08_static_route_vrf_omission`: MISSED (unexercised VRF subnet)
  - `C09_unrecognized_device_in_fence`: DETECTED (Resolver)
  - `C10_direction_in_out_swap`: MISSED (egress traffic unmonitored by baseline probe)
  - *Detection Rate*: **7/10 (70.0%)**.

### Check 9: Sequence Shape & Contract Integrity
- **Lengths**: All 40 test sequences have length exactly 50.
- **Validation**: 2,000/2,000 Intent objects strictly validate against `agent.contract.models.Intent` with `extra="forbid"`.
- **Length Support**: Generator supports lengths 50, 75, 100, 150, 200, and rejects lengths outside [10, 200].
- **Coverage**: No sequences of length $\ge 100$ were tested or included in the frozen benchmark dataset.

---

## 3. BUGS AND DISCREPANCIES FOUND

1. **Frozen Dataset Desynchronization from Current Codebase**:
   - **Files**: [generator/topology/generator.py](file:///D:/Computer_networks_proj/generator/topology/generator.py#L88-L109), [generator/data/test/*.jsonl](file:///D:/Computer_networks_proj/generator/data/test/)
   - **Problem**: Baseline ACL deny rules (ports 22, 23, 3389) were moved from edge routers to firewall routers in `generator/topology/generator.py` after the bulk frozen dataset was built. As a result, newly regenerated sequences differ on 40/40 test files compared to `generator/data/test/`.
2. **Missing `isolation_leak` Traps in Frozen Benchmark**:
   - **Files**: [generator/traps/isolation_leak.py](file:///D:/Computer_networks_proj/generator/traps/isolation_leak.py), [generator/data/test/*.manifest.json](file:///D:/Computer_networks_proj/generator/data/test/)
   - **Problem**: The frozen test dataset contains **0 isolation leak traps** across all 40 sequences (568 prefix overlap, 32 shadowing, 0 isolation leak). The trap generator silently failed proof verification during the initial build due to an undefined variable `leaking_route`.
3. **Replay Checker Invariant Accumulation Bug**:
   - **Files**: [generator/replay_check.py](file:///D:/Computer_networks_proj/generator/replay_check.py#L37-L68)
   - **Problem**: In `replay_single_sequence`, `cumulative_invariants` is initialized to `list(spec.baseline_invariants)` but never accumulates the new invariants added at each step. During replay, it only verifies baseline invariants, missing potential regressions of earlier step invariants.
4. **Sidecar Manifest Lacks Full Invariant Models**:
   - **Files**: [generator/sequences/sidecar.py](file:///D:/Computer_networks_proj/generator/sequences/sidecar.py#L33)
   - **Problem**: `SequenceManifest.invariants_added` maps step index strings to invariant ID strings (`dict[str, str]`) rather than full invariant objects or dictionaries, making it impossible for replay scripts to inspect or reconstruct step invariants directly from the manifest without regenerating the sequence.
5. **Repetitive Synthetic Corruptions**:
   - **Files**: [generator/replay_check.py](file:///D:/Computer_networks_proj/generator/replay_check.py#L113-L165)
   - **Problem**: The corruption suite cycles through only 4 synthetic operations rather than 50 varied network mutations.

---

## 4. NOT VERIFIED

1. **Batfish Oracle & Batfish Service Container**:
   - `pybatfish` is not installed in the local Python environment (`ModuleNotFoundError: No module named 'pybatfish'`), and no active Batfish Docker container was running. All Batfish-dependent tests were cleanly skipped.
2. **Sequences of Length 100–200 in Benchmark Evaluation**:
   - While `build_sequence` was verified to support lengths up to 200, no sequences longer than 50 steps exist in `generator/data/` or were tested in benchmark evaluations.

---

## 5. Auditor Verdict

**VERDICT: CONDITIONAL PASS / NOT SAFE FOR IMMEDIATE FINAL EVALUATION**

**Rationale**: While the architecture, schema contracts, and prompt-safe isolation mechanisms are structurally sound, the frozen test files in `generator/data/test/` **must be regenerated** (`python -m generator.build --bulk-frozen --out generator/data`) to reflect the corrected router baseline ACLs, re-incorporate `isolation_leak` traps (currently 0% in test data), and ensure `FROZEN.json` matches current code outputs.
