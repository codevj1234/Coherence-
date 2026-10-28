"""Verify naive vs gold diffs and independent oracle outcomes for sample traps."""

import difflib
import json
from pathlib import Path
from audit.independent_checker import IndependentAuditChecker
from generator.topology.catalog import get_topology
from generator.topology.generator import generate_topology_configs
from agent.contract.models import Invariant, Intent

def main():
    print("=== SAMPLE TRAP DIFFS & PROOF VERIFICATION ===")
    checker = IndependentAuditChecker()

    # Sample 1: Shadowing trap: topo_test_large_test_seq_003 step 12
    # Naive edit prepends 'permit tcp any any eq 993' to te_l_r48 EDGE_IN
    # Broken invariant: topo_test_large_test_seq_003_step_010_deny_tcp_993
    print("\n--- Trap 1 (Shadowing): topo_test_large_test_seq_003 step 12 ---")
    seq_file = Path("generator/data/test/topo_test_large_test_seq_003.jsonl")
    intents = [Intent.model_validate_json(line) for line in seq_file.open() if line.strip()]
    intent12 = intents[12]
    gold_patch12 = intent12.gold_patch
    
    spec = get_topology("topo_test_large")
    configs = generate_topology_configs(spec)
    for i in range(12):
        configs.update(intents[i].gold_patch)

    curr_cfg = configs["te_l_r48"]
    gold_cfg = gold_patch12["te_l_r48"]
    
    # Naive edit: careless broad permit at top
    naive_cfg = curr_cfg.replace("ip access-list extended EDGE_IN\n", "ip access-list extended EDGE_IN\n permit tcp any any eq 993\n")

    print("\n[GOLD PATCH DIFF for te_l_r48]:")
    print("\n".join(difflib.unified_diff(curr_cfg.splitlines(), gold_cfg.splitlines(), lineterm="")))

    print("\n[NAIVE EDIT DIFF for te_l_r48]:")
    print("\n".join(difflib.unified_diff(curr_cfg.splitlines(), naive_cfg.splitlines(), lineterm="")))

    # Invariant broken: deny_tcp_993 from step 10
    inv_broken = Invariant(
        id="topo_test_large_test_seq_003_step_010_deny_tcp_993",
        kind="isolate",
        source=intents[10].params["src"],
        destination=intents[10].params["dst"],
        headers={"protocol": "tcp", "dst_port": 993},
        created_at_step=10,
    )

    # Check under naive edit
    naive_configs = dict(configs)
    naive_configs["te_l_r48"] = naive_cfg
    res_naive = checker.check(naive_configs, [inv_broken])
    print(f"Naive edit evaluation on broken invariant ({inv_broken.id}): {res_naive[inv_broken.id]} (Expected False - Broken)")

    # Check under gold patch
    gold_configs = dict(configs)
    gold_configs["te_l_r48"] = gold_cfg
    res_gold = checker.check(gold_configs, [inv_broken])
    print(f"Gold patch evaluation on broken invariant ({inv_broken.id}): {res_gold[inv_broken.id]} (Expected True - Preserved)")

    # Sample 2: Prefix overlap trap: topo_test_large_test_seq_001 step 10
    print("\n--- Trap 2 (Prefix Overlap): topo_test_large_test_seq_001 step 10 ---")
    seq_file1 = Path("generator/data/test/topo_test_large_test_seq_001.jsonl")
    intents1 = [Intent.model_validate_json(line) for line in seq_file1.open() if line.strip()]
    intent10 = intents1[10]
    gold_patch10 = intent10.gold_patch

    configs1 = generate_topology_configs(spec)
    for i in range(10):
        configs1.update(intents1[i].gold_patch)

    curr_cfg1 = configs1["te_l_r1"]
    gold_cfg1 = gold_patch10["te_l_r1"]
    naive_cfg1 = curr_cfg1.replace("ip access-list extended EDGE_IN\n", "ip access-list extended EDGE_IN\n deny ip 10.51.1.0 0.0.0.255 any\n")

    print("\n[GOLD PATCH DIFF for te_l_r1]:")
    print("\n".join(difflib.unified_diff(curr_cfg1.splitlines(), gold_cfg1.splitlines(), lineterm="")))

    print("\n[NAIVE EDIT DIFF for te_l_r1]:")
    print("\n".join(difflib.unified_diff(curr_cfg1.splitlines(), naive_cfg1.splitlines(), lineterm="")))

    inv_broken1 = Invariant(
        id="topo_test_large_inv_01_reach_tcp_80",
        kind="reach",
        source="10.51.1.10",
        destination="10.51.2.10",
        headers={"protocol": "tcp", "dst_port": 80},
        created_at_step=0,
    )

    naive_configs1 = dict(configs1)
    naive_configs1["te_l_r1"] = naive_cfg1
    res_naive1 = checker.check(naive_configs1, [inv_broken1])
    print(f"Naive edit evaluation on broken invariant ({inv_broken1.id}): {res_naive1[inv_broken1.id]} (Expected False - Broken)")

    gold_configs1 = dict(configs1)
    gold_configs1["te_l_r1"] = gold_cfg1
    res_gold1 = checker.check(gold_configs1, [inv_broken1])
    print(f"Gold patch evaluation on broken invariant ({inv_broken1.id}): {res_gold1[inv_broken1.id]} (Expected True - Preserved)")

if __name__ == "__main__":
    main()
