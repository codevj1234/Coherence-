"""Check whether baseline invariants regress during replay across all 40 frozen sequences."""

import json
from pathlib import Path
from audit.independent_checker import IndependentAuditChecker
from generator.topology.catalog import get_topology
from generator.topology.generator import generate_topology_configs
from agent.contract.models import Intent

def main():
    data_dir = Path("generator/data/test")
    jsonl_files = sorted(list(data_dir.glob("*.jsonl")))
    jsonl_files = [f for f in jsonl_files if not f.name.endswith(".prompt_safe.jsonl")]

    checker = IndependentAuditChecker()
    total_regressions = 0

    for idx, jf in enumerate(jsonl_files, 1):
        mf = jf.with_suffix(".manifest.json")
        m = json.load(mf.open())
        topo_id = m["topology_id"]
        spec = get_topology(topo_id)
        configs = generate_topology_configs(spec)
        baseline_invs = list(spec.baseline_invariants)

        intents = [Intent.model_validate_json(line) for line in jf.open() if line.strip()]
        seq_regressions = 0

        for step_idx, intent in enumerate(intents):
            configs.update(intent.gold_patch)
            res = checker.check(configs, baseline_invs)
            broken = [inv.id for inv in baseline_invs if not res[inv.id]]
            if broken:
                print(f"  [{m['sequence_id']}] Step {step_idx}: broken baseline invariants: {broken}")
                seq_regressions += len(broken)

        print(f"[{idx:02d}/40] {m['sequence_id']}: {seq_regressions} baseline regressions.")
        total_regressions += seq_regressions

    print(f"\nTotal baseline regressions across all 40 sequences: {total_regressions}")

if __name__ == "__main__":
    main()
