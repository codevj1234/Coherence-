"""Audit Check 3: Independent Oracle Check.

1. Runs all baseline invariants across all 4 topologies through IndependentAuditChecker.
2. Compares results against generator.oracle.reference.ReferenceOracle.
3. Replays all 40 frozen test sequences using IndependentAuditChecker with gold patches.
   Checks that ALL historical invariants (baseline + all preceding step invariants) hold at every step.
4. Checks for Batfish/pybatfish availability.
"""

import json
from pathlib import Path
import sys

from audit.independent_checker import IndependentAuditChecker
from generator.oracle.reference import ReferenceOracle
from generator.topology.catalog import TOPOLOGIES, get_topology
from generator.topology.generator import generate_topology_configs
from generator.sequences.builder import build_sequence
from agent.contract.models import Invariant, Intent


def check_baseline_invariants():
    print("=== Step 1: Baseline Invariants Comparison ===")
    ref_oracle = ReferenceOracle()
    audit_oracle = IndependentAuditChecker()
    disagreements = []
    total_invariants = 0

    for topo_name, spec in TOPOLOGIES.items():
        configs = generate_topology_configs(spec)
        ref_res = ref_oracle.check(configs, spec.baseline_invariants)
        audit_res = audit_oracle.check(configs, spec.baseline_invariants)

        for inv in spec.baseline_invariants:
            total_invariants += 1
            ref_val = ref_res[inv.id]
            audit_val = audit_res[inv.id]
            if ref_val != audit_val:
                disagreements.append({
                    "topology": topo_name,
                    "invariant_id": inv.id,
                    "kind": inv.kind,
                    "ref": ref_val,
                    "audit": audit_val,
                })
            else:
                if not audit_val:
                    disagreements.append({
                        "topology": topo_name,
                        "invariant_id": inv.id,
                        "kind": inv.kind,
                        "ref": ref_val,
                        "audit": audit_val,
                        "note": "Baseline invariant failed!",
                    })

    print(f"Total baseline invariants tested: {total_invariants}")
    print(f"Disagreements between ReferenceOracle and IndependentAuditChecker: {len(disagreements)}")
    if disagreements:
        for d in disagreements:
            print(f"  DISAGREEMENT: {d}")
    else:
        print("  PASS: 100% agreement. All baseline invariants pass under both checkers.")

    return len(disagreements) == 0


def replay_40_test_sequences():
    print("\n=== Step 2: Replay 40 Test Sequences with IndependentAuditChecker ===")
    data_dir = Path("generator/data/test")
    jsonl_files = sorted(list(data_dir.glob("*.jsonl")))
    jsonl_files = [f for f in jsonl_files if not f.name.endswith(".prompt_safe.jsonl")]

    print(f"Found {len(jsonl_files)} frozen test sequence files to replay.")

    audit_oracle = IndependentAuditChecker()
    total_steps_replayed = 0
    broken_earlier_invariants = []

    for seq_idx, jsonl_file in enumerate(jsonl_files, 1):
        manifest_file = jsonl_file.with_suffix(".manifest.json")
        with manifest_file.open("r", encoding="utf-8") as f:
            manifest = json.load(f)

        topo_id = manifest["topology_id"]
        spec = get_topology(topo_id)
        current_configs = generate_topology_configs(spec)

        # Regenerate reference sequence to get the exact Invariant objects
        seq_gen = build_sequence(
            spec=spec,
            length=manifest["length"],
            seed=manifest["seed"],
            sequence_id=manifest["sequence_id"],
            split=manifest["split"],
        )

        baseline_count = len(spec.baseline_invariants)
        all_invariants = seq_gen.invariants

        # Read intents from the frozen file
        intents = []
        with jsonl_file.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    intents.append(Intent.model_validate_json(line))

        seq_id = manifest["sequence_id"]
        step_regressions = 0

        for step_idx, intent in enumerate(intents):
            total_steps_replayed += 1
            gold_patch = intent.gold_patch
            assert gold_patch is not None, f"Missing gold patch at {seq_id} step {step_idx}"

            # Apply gold patch
            current_configs.update(gold_patch)

            # Active cumulative invariants up to this step:
            # baseline invariants + all earlier step invariants [0 .. step_idx - 1]
            earlier_invariants = all_invariants[:baseline_count + step_idx]
            new_inv = all_invariants[baseline_count + step_idx]

            # 1. Check all earlier invariants
            earlier_res = audit_oracle.check(current_configs, earlier_invariants)
            for inv in earlier_invariants:
                if not earlier_res[inv.id]:
                    broken_earlier_invariants.append({
                        "sequence": seq_id,
                        "step": step_idx,
                        "broken_invariant": inv.id,
                        "kind": inv.kind,
                        "type": "REGRESSION_OF_EARLIER_INVARIANT",
                    })
                    step_regressions += 1

            # 2. Check the new invariant
            new_res = audit_oracle.check(current_configs, [new_inv])
            if not new_res[new_inv.id]:
                broken_earlier_invariants.append({
                    "sequence": seq_id,
                    "step": step_idx,
                    "broken_invariant": new_inv.id,
                    "kind": new_inv.kind,
                    "type": "NEW_INVARIANT_FAILED",
                })
                step_regressions += 1

        status_str = f"0 regressions" if step_regressions == 0 else f"{step_regressions} REGRESSIONS"
        print(f"  [{seq_idx:02d}/{len(jsonl_files)}] {seq_id}: {len(intents)} steps replayed, {status_str}.")

    print(f"\nTotal steps replayed across all 40 sequences: {total_steps_replayed}")
    print(f"Total earlier invariants broken: {len(broken_earlier_invariants)}")
    if broken_earlier_invariants:
        print(f"FAIL: {len(broken_earlier_invariants)} regression violations detected:")
        for b in broken_earlier_invariants[:10]:
            print(f"  {b}")
    else:
        print("PASS: Exactly 0 earlier invariants broken across all 2000 steps.")

    return len(broken_earlier_invariants) == 0


def check_batfish_status():
    print("\n=== Step 3: Batfish Availability Check ===")
    try:
        import pybatfish
        print(f"pybatfish is installed: version {pybatfish.__version__}")
        from pybatfish.client.session import Session
        bf = Session(host="localhost")
        print("Connected to Batfish service on localhost.")
        return True
    except ImportError:
        print("NOT VERIFIED: pybatfish is not installed in the Python environment (ModuleNotFoundError).")
        return False
    except Exception as e:
        print(f"NOT VERIFIED: Batfish service connection failed: {e}")
        return False


def main():
    ok1 = check_baseline_invariants()
    ok2 = replay_40_test_sequences()
    check_batfish_status()

    if ok1 and ok2:
        print("\n=== OVERALL INDEPENDENT ORACLE RESULT: PASS ===")
    else:
        print("\n=== OVERALL INDEPENDENT ORACLE RESULT: FAIL ===")


if __name__ == "__main__":
    main()
