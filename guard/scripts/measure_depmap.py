"""Dependency-map miss rate and savings in harness runs (roadmap gap 4), all intent types, all Zoo beds.

Runs the simulated small model open-loop (C0) so mistakes - including routing mistakes (route filters, OSPF
costs, VRFs) - are committed, and reports, per intent type:
  regressions  - invariants newly broken by a committed step
  misses       - of those, how many the dependency map did not flag as affected (the guard would not re-check them)
  checked      - mean share of tracked invariants the map flags per step (lower = more re-checking saved)
The judge here is the reference engine; scripts/validate_depmap_batfish.py checks the map against Batfish.

    python3 scripts/measure_depmap.py [N_SEQ=5] [LENGTH=60]
"""
import json, os, sys
from collections import defaultdict
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
from cuc.harness import run_sequence
from cuc.intents import generate_sequence
from cuc.invariants import ReferenceBackend
from cuc.models import SimModel
from cuc.testbed import STANDARD_BEDS, get_testbed

n_seq = int(sys.argv[1]) if len(sys.argv) > 1 else 5
length = int(sys.argv[2]) if len(sys.argv) > 2 else 60
B = ReferenceBackend()
model = SimModel("sim-small", p_broad=0.45, p_misplace=0.35, p_syntax=0.05)
agg = defaultdict(lambda: defaultdict(float))
for bed in STANDARD_BEDS:
    tb = get_testbed(bed)
    for s in range(n_seq):
        its, _ = generate_sequence(500 + s, length, B, testbed=tb)
        for r in run_sequence(its, model, "C0", B, f"dm-{bed}-{s}", testbed=tb):
            a = agg[r["intent_type"]]
            a["steps"] += 1
            a["regressions"] += len(r["broken_invariants"])
            a["misses"] += r["dep_map_miss"]
            a["checked_share"] += r["affected_checked"] / max(1, r.get("tracked_n", 0) or 1)
            a["affected"] += r["affected_checked"]
            a["tracked"] += r.get("tracked_n", 0)
rows = []
print(f"{'intent type':14} {'steps':>6} {'regressions':>11} {'misses':>6} {'checked share':>13}")
tot = defaultdict(float)
for t in sorted(agg):
    a = agg[t]
    share = a["affected"] / a["tracked"] if a["tracked"] else float("nan")
    print(f"{t:14} {int(a['steps']):6d} {int(a['regressions']):11d} {int(a['misses']):6d} {share:13.1%}")
    for k in ("steps", "regressions", "misses", "affected", "tracked"):
        tot[k] += a[k]
    rows.append(dict(intent_type=t, steps=int(a["steps"]), regressions=int(a["regressions"]), misses=int(a["misses"]), checked_share=round(share, 4)))
share = tot["affected"] / tot["tracked"]
print(f"{'ALL':14} {int(tot['steps']):6d} {int(tot['regressions']):11d} {int(tot['misses']):6d} {share:13.1%}")
os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
json.dump(dict(rows=rows, n_seq=n_seq, length=length, model=model.name, judge="reference"),
          open(os.path.join(ROOT, "results", "depmap_measurement.json"), "w"), indent=1)
