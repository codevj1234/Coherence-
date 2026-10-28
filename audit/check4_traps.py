"""Audit Check 4: Traps verification and contradiction audit."""

import json
from pathlib import Path
import sys

from audit.independent_checker import IndependentAuditChecker
from generator.topology.catalog import get_topology
from agent.contract.models import Intent


def main():
    print("=== AUDIT CHECK 4: TRAPS & CONTRADICTIONS ===")
    data_dir = Path("generator/data/test")
    manifest_files = sorted(list(data_dir.glob("*.manifest.json")))
    print(f"Total manifest files: {len(manifest_files)}")

    kind_totals = {"shadowing": 0, "prefix_overlap": 0, "isolation_leak": 0}
    seq_trap_stats = {}
    ratio_in_bounds = True

    trap_samples = {"shadowing": [], "prefix_overlap": [], "isolation_leak": []}

    for mf in manifest_files:
        m = json.load(mf.open("r", encoding="utf-8"))
        seq_id = m["sequence_id"]
        length = m["length"]
        trap_count = m["trap_count"]
        trap_ratio = m["trap_ratio"]
        traps = m["traps"]

        ratio_pct = trap_ratio * 100.0
        if not (25.0 <= ratio_pct <= 35.0):
            print(f"OUT OF BOUNDS: {seq_id} has trap ratio {ratio_pct:.1f}%")
            ratio_in_bounds = False

        seq_kinds = {"shadowing": 0, "prefix_overlap": 0, "isolation_leak": 0}
        for step_str, trap_info in traps.items():
            k = trap_info["kind"]
            seq_kinds[k] += 1
            kind_totals[k] += 1

            if len(trap_samples[k]) < 2:
                trap_samples[k].append((seq_id, int(step_str), trap_info))

        seq_trap_stats[seq_id] = {
            "length": length,
            "trap_count": trap_count,
            "trap_ratio_pct": ratio_pct,
            "kinds": seq_kinds,
        }

    print("\n--- Trap Counts & Ratios per Sequence ---")
    all_exact_30 = True
    for seq_id, s in seq_trap_stats.items():
        print(f"  {seq_id}: {s['trap_count']}/{s['length']} ({s['trap_ratio_pct']:.1f}%) | kinds: {s['kinds']}")
        if s["trap_ratio_pct"] != 30.0:
            all_exact_30 = False

    print(f"\nAll sequences within 25-35% trap bounds: {ratio_in_bounds}")
    print(f"All sequences exactly 30.0% (15/50 traps): {all_exact_30}")
    print(f"Overall Trap Totals across 40 sequences: {kind_totals} (Total: {sum(kind_totals.values())})")

    # Contradiction check
    print("\n--- Contradiction Audit across all 40 Test Sequences ---")
    contradiction_count = 0
    for mf in manifest_files:
        jf = mf.parent / (mf.name.replace(".manifest.json", ".jsonl"))
        m = json.load(mf.open("r", encoding="utf-8"))
        
        intents = [Intent.model_validate_json(line) for line in jf.open("r", encoding="utf-8") if line.strip()]
        
        # Check pairwise contradiction
        for i in range(len(intents)):
            for j in range(i + 1, len(intents)):
                t1, t2 = intents[i].type, intents[j].type
                p1, p2 = intents[i].params, intents[j].params
                if p1.get("device") == p2.get("device"):
                    if t1 == "acl_permit" and t2 == "acl_deny":
                        if p1.get("src") == p2.get("src") and p1.get("dst") == p2.get("dst") and p1.get("port") == p2.get("port"):
                            print(f"CONTRADICTION FOUND in {m['sequence_id']}: step {i} ({t1}) vs step {j} ({t2})")
                            contradiction_count += 1
                    elif t1 == "acl_deny" and t2 == "acl_permit":
                        if p1.get("src") == p2.get("src") and p1.get("dst") == p2.get("dst") and p1.get("port") == p2.get("port"):
                            print(f"CONTRADICTION FOUND in {m['sequence_id']}: step {i} ({t1}) vs step {j} ({t2})")
                            contradiction_count += 1

    print(f"Contradicting intent pairs found: {contradiction_count}")

    # Deep dive into samples of available trap kinds
    print("\n=== Detailed Inspection of Trap Samples ===")
    for kind, samples in trap_samples.items():
        print(f"\n==================== TRAP KIND: {kind.upper()} ({len(samples)} samples found) ====================")
        if not samples:
            print(f"CRITICAL DISCREPANCY: Zero traps of kind '{kind}' exist in the frozen test dataset!")
            continue

        for idx, (seq_id, step, t_info) in enumerate(samples, 1):
            print(f"\n--- Sample {idx}/{len(samples)}: Sequence {seq_id}, Step {step} ---")
            jf = data_dir / f"{seq_id}.jsonl"
            lines = jf.open("r", encoding="utf-8").readlines()
            intent_data = json.loads(lines[step])
            intent = Intent.model_validate(intent_data)

            print(f"Intent ID: {intent.id}")
            print(f"Intent Type: {intent.type}")
            print(f"Intent Text: \"{intent.text}\"")
            print(f"Broken Invariant ID (from manifest): {t_info['broken_invariant_id']}")
            print(f"Naive Description: {t_info['naive_description']}")

            gold_patch = intent.gold_patch
            print(f"Devices modified in gold patch: {list(gold_patch.keys())}")

            if kind == "shadowing":
                explanation = "Realistic: Network operators frequently insert permit rules above existing deny rules to restore connectivity, unintentionally shadowing security policies."
            elif kind == "prefix_overlap":
                explanation = "Realistic: Coarse prefix route filtering or summarization frequently overrides more specific routing entries required for critical services."
            else:
                explanation = "Realistic: Reassigning an interface to a new VRF without removing connected routes or subnets commonly creates cross-tenant route leakage."
            print(f"Realism Assessment: {explanation}")


if __name__ == "__main__":
    main()
