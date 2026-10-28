# Change-Sequence Generator (Phase 2)

Change-sequence generator for the **Coherence Under Change** research project. This package deterministically generates long sequences (50–200 steps) of incremental network policy intents, complete with verified ground-truth edits (*gold patches*), baseline invariants, automated trap injections, sidecar metadata manifests, and prompt-safe sequence views.

---

## 1. Directory Structure

```
generator/
├── __init__.py
├── README.md                     # Phase 2 documentation and architecture guide
├── build.py                      # CLI tool to generate sequences and bulk frozen datasets
├── verify_frozen.py              # CLI tool to verify SHA-256 integrity of frozen datasets
├── replay_check.py               # CLI tool to replay sequences and test corruption detection
├── data/                         # Generated datasets and frozen benchmark manifests
│   ├── FROZEN.json               # SHA-256 hash manifest of all 40 frozen test sequences
│   ├── train/                    # Training sequences (*.jsonl, *.manifest.json, *.prompt_safe.jsonl)
│   └── test/                     # 40 frozen evaluation sequences
├── topology/                     # Topology definitions and baseline configuration generator
│   ├── __init__.py
│   ├── spec.py                   # Data models (TopologySpec, NodeRole, HostSubnet, Link)
│   ├── generator.py              # Deterministic Cisco IOS config and invariant synthesizer
│   └── catalog.py                # 4 benchmark topologies (2 train, 2 test)
├── oracle/                       # Network policy verification oracles
│   ├── __init__.py
│   ├── base.py                   # BaseOracle abstract base class and FailureDetail model
│   ├── mock.py                   # Deterministic MockOracle for unit testing
│   ├── reference.py              # Pure-Python ReferenceOracle packet tracer
│   └── batfish.py                # BatfishOracle adapter (graceful skip when pybatfish absent)
├── intents/                      # Intent synthesis and natural language templates
│   ├── __init__.py
│   ├── paraphrases.py            # Train and test paraphrase pools (strictly disjoint)
│   └── templates.py              # Generator functions for all 5 intent contract types
├── traps/                        # Regression trap construction and automated proofs
│   ├── __init__.py
│   ├── catalog.py                # Unified trap synthesis and scheduling coordinator
│   ├── shadowing.py              # ACL shadowing trap generator and proof verifier
│   ├── prefix_overlap.py         # Route/prefix-filter overlap trap generator and proof
│   └── isolation_leak.py         # VRF cross-leak trap generator and proof
└── sequences/                    # Deterministic sequence assembly
    ├── __init__.py
    ├── builder.py                # SequenceBuilder and build_sequence logic
    ├── contradictions.py         # Contradiction and collision prevention engine
    └── sidecar.py                # JSONL, sidecar manifest, and prompt-safe file serialization
```

---

## 2. Cisco IOS Configuration Subset

Configurations generated and parsed by this package adhere strictly to standard Cisco IOS syntax:

- **VRFs**:
  ```ios
  ip vrf RED
   rd 65000:10
  !
  interface GigabitEthernet0/1
   ip vrf forwarding RED
   ip address 10.10.1.1 255.255.255.0
   no shutdown
  !
  ```
- **Extended ACLs**:
  ```ios
  ip access-list extended EDGE_IN
   permit tcp 10.10.1.0 0.0.0.255 10.10.2.0 0.0.0.255 eq 80
   deny tcp any any eq 22
   permit ip any any
  !
  ```
- **Prefix-Lists & Route-Maps**:
  ```ios
  ip prefix-list PFX_ALLOW seq 10 permit 10.20.0.0/16 ge 24 le 24
  !
  route-map RM_FILTER permit 10
   match ip address prefix-list PFX_ALLOW
  !
  ```
- **Static Routes**:
  ```ios
  ip route 10.20.1.0 255.255.255.0 10.0.0.2
  ip route vrf RED 10.30.1.0 255.255.255.0 10.0.1.2
  ```

All generated device configurations round-trip cleanly through the agent's `parse_model_output` and `resolve_devices` pipeline with zero warnings and zero errors.

---

## 3. Verification Oracle

The oracle suite evaluates whether proposed device configurations satisfy required policy invariants:

1. **`ReferenceOracle`**: A pure-Python packet tracer and routing simulation engine:
   - Evaluates network reachability (`reach`), isolation (`isolate`), and transit waypoint traversal (`waypoint`).
   - Models hop-by-hop forwarding using longest prefix match (LPM) on VRF-aware static routing tables.
   - Evaluates ingress and egress extended ACLs with first-match semantics and implicit deny.
   - Traces packet paths and detects routing loops, unreachable subnets, and waypoint bypasses.
   - Generates actionable `FailureDetail` reports explaining root-cause blocking devices, ACL rules, and line numbers.
2. **`MockOracle`**: Configurable mock oracle for lightning-fast testing and offline unit verification.
3. **`BatfishOracle`**: Adapter interface for Batfish network analysis. Safely detects if `pybatfish` is uninstalled or the Batfish Docker container is unreachable, raising a clean skip rather than breaking CI.

---

## 4. Intent Types and Disjoint Paraphrases

All five intent types defined in the shared contract (`agent.contract.models.IntentType`) are supported:

| Intent Type | Operation | Parameter Schema | Invariant Kind Introduced |
| :--- | :--- | :--- | :--- |
| `acl_permit` | Permit traffic on target port/protocol | `device`, `src`, `dst`, `proto`, `port` | `reach` |
| `acl_deny` | Deny unwanted traffic flow | `device`, `src`, `dst`, `proto`, `port` | `isolate` |
| `vrf_isolate` | Ensure cross-VRF isolation | `device`, `vrf`, `src`, `dst` | `isolate` |
| `prefix_filter` | Filter route advertisements | `device`, `prefix`, `masklen`, `route_map` | `reach` or `isolate` |
| `waypoint` | Enforce transit via inspection node | `ingress_dev`, `waypoint_dev`, `src`, `dst` | `waypoint` |

### Train vs. Test Disjointness
To test model generalization:
- Paraphrase pools for train and test splits are strictly disjoint.
- Unique sentence patterns and reserved vocabulary (e.g. `"strictly drop"`, `"ensure traffic traverses"`, `"prohibit all packets"`, `"guarantee packet traversal"`) are reserved exclusively for the evaluation test set and never appear in training sequences.
- An automated unit test (`test_train_test_disjointness` and `test_test_reserved_phrases_not_in_train`) continuously enforces this separation.

---

## 5. Regression Traps & Automated Proofs

To measure LLM policy drift and regression resistance, 25%–35% (nominal 30%) of sequence steps are designed as subtle traps:

1. **`shadowing`**: A broad permit rule that, if placed naively at the top of an ACL, shadows an earlier specific deny rule, violating a prior isolation invariant.
2. **`prefix_overlap`**: A coarse prefix filter that satisfies a new policy but accidentally drops a more specific route required by an earlier reachability invariant.
3. **`isolation_leak`**: A naive route injection or interface VRF change that inadvertently bridges two isolated VRFs.

### Automated Proof Requirement
Every generated trap is verified via an automated two-stage proof:
1. **Regression Proof**: The naive edit is applied and evaluated against all historical invariants; it **must fail** at least one prior invariant.
2. **Gold Patch Proof**: The gold patch is applied and evaluated against all historical invariants PLUS the new invariant; it **must pass all** invariants.

Any candidate trap failing either proof is automatically rejected and retried with fresh parameters.

---

## 6. Manifest Sidecar & File Formats

When a sequence is generated, three files are written:

1. **`<sequence_id>.jsonl`**: The complete sequence of `Intent` records, including `gold_patch` dictionaries, for offline training or benchmark oracle evaluation.
2. **`<sequence_id>.manifest.json`**: A detailed sidecar manifest containing:
   - Topology metadata, initial configurations, and baseline invariants.
   - Step-by-step breakdown: intent text, gold patches, naive edits, trap classification, and broken invariant IDs.
3. **`<sequence_id>.prompt_safe.jsonl`**: A sanitized version where `gold_patch` is set to `null`. This prevents test-time data contamination when prompts are built for LLM agents.
4. **`FROZEN.json`**: Benchmark registry recording SHA-256 hashes of all 40 frozen test sequences for reproducibility.

---

## 7. CLI Usage

### Generate Single Sequence or Bulk Frozen Benchmark Dataset
```bash
# Generate single sequence
python -m generator.build --topo topo_test_small --length 50 --split test --seed 42 --out generator/data

# Generate all 40 frozen benchmark test sequences (plus 10 train sequences)
python -m generator.build --bulk-frozen --out generator/data
```

### Verify Frozen Benchmark Integrity
```bash
python -m generator.verify_frozen --manifest generator/data/FROZEN.json
```

### Run Replay & Corruption Verification Suite
```bash
# Replay 40 test sequences with gold patches and verify 100% corruption detection
python -m generator.replay_check --data-dir generator/data --corruptions 50
```

---

## 8. Decisions

1. **Deterministic Randomness**: All sequence generation, intent ordering, IP allocation, and trap scheduling rely exclusively on `random.Random(seed)`. No global random state or unseeded entropy sources are used. Subprocess tests under different `PYTHONHASHSEED` values confirm byte-for-byte reproducibility.
2. **Centralized Baseline Invariant Enforcement**: Central security devices (e.g., firewall `fw1`) maintain baseline restrictions (e.g. SSH/Telnet/RDP management isolation), allowing shadowing traps to trigger verified cross-zone regressions.
3. **Strict Disjointness Guarantee**: Paraphrase templates and keyword allocations between train and test splits are checked statically in unit tests to guarantee zero test-leakage during agent training.
4. **Prompt-Safe Separation**: Gold patches are stripped in `*.prompt_safe.jsonl` views. The prompt builder structurally enforces this by rejecting any intent object that possesses a `gold_patch` attribute.
5. **Pure-Python Reference Oracle**: The benchmark ships with an autonomous packet tracer (`ReferenceOracle`) so all sequences can be generated and verified immediately in any standard Python 3.11+ environment without requiring external Java/Batfish containers.

---

## 9. Known Limits

1. **Static Routing Abstraction**: Dynamic routing protocols (BGP/OSPF convergence, route redistribution metrics) are modeled via static route tables, prefix lists, and route maps in `ReferenceOracle`.
2. **Batfish Oracle Container Dependency**: `BatfishOracle` requires an active Batfish service container. When Batfish is unavailable, `test_batfish_oracle_skip` skips gracefully without failing the test suite. Full Batfish re-verification can be executed once the team's shared container environment is configured.

---

## 10. Proposed Contract Changes

**None.** The generator strictly conforms to the existing contract models (`agent.contract.models`), producing valid `Intent`, `Invariant`, and `DeviceConfig` records. No changes to the contract schemas or agent harness were required.
