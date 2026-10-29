"""Write every standard testbed to testbeds/<name>/ (configs/*.cfg, invariants.json, meta.json).
Useful for loading a bed into Batfish by hand and for teammates to inspect the networks."""
import json, os, sys
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from cuc.testbed import STANDARD_BEDS, get_testbed
from cuc.topology import write_snapshot

for name in STANDARD_BEDS:
    tb = get_testbed(name)
    d = os.path.join(ROOT, "testbeds", name)
    write_snapshot(tb.baseline, d)
    json.dump([i.__dict__ for i in tb.invariants], open(os.path.join(d, "invariants.json"), "w"), indent=1, sort_keys=True)
    json.dump(tb.meta, open(os.path.join(d, "meta.json"), "w"), indent=1, sort_keys=True)
    print(f"{name:9} devices={tb.meta['devices']:3} core_nodes={tb.meta['core_nodes']:3} invariants={len(tb.invariants)} -> {d}")
