"""Generate the frozen test sequences and run C0/C1/C3 (plus oracle-reset) for two simulated models."""
import json, os, sys, time
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from cuc.harness import run_sequence, write_jsonl
from cuc.intents import generate_sequence, read_sequence, write_sequence
from cuc.invariants import ReferenceBackend
from cuc.models import SimModel
from cuc.testbed import get_testbed

TB_NAME = os.environ.get("TESTBED", "legacy9")           # legacy9 | abilene | bteurope | cesnet
TB = get_testbed(TB_NAME)
PFX = "" if TB_NAME in ("legacy", "legacy9") else f"{TB_NAME}_"

N_SEQ = int(os.environ.get("N_SEQ", 20))
LENGTH = int(os.environ.get("LENGTH", 50))
SEEDS = list(range(100, 100 + N_SEQ))           # test seeds, frozen
backend = ReferenceBackend()
os.makedirs(f"{ROOT}/sequences", exist_ok=True)
os.makedirs(f"{ROOT}/results", exist_ok=True)

t0 = time.time()
seqs, metas = [], []
for s in SEEDS:
    path = f"{ROOT}/sequences/{PFX}test_seed{s}.jsonl"
    its, meta = generate_sequence(s, LENGTH, backend, testbed=None if not PFX else TB)
    write_sequence(path, its, meta)
    seqs.append(its); metas.append(meta)
print(f"generated {len(seqs)} sequences x {LENGTH} steps in {time.time()-t0:.1f}s; "
      f"mean trap ratio {sum(m['trap_ratio'] for m in metas)/len(metas):.2f}")

MODELS = [SimModel("sim-small", p_broad=0.45, p_misplace=0.35, p_syntax=0.05),
          SimModel("sim-large", p_broad=0.15, p_misplace=0.12, p_syntax=0.01)]
out = f"{ROOT}/results/{PFX}steps.jsonl"
open(out, "w").close()
for m in MODELS:
    for cond, mode in [("C0", "open"), ("C0", "oracle_reset"), ("C1", "open"), ("C3", "open")]:
        t1 = time.time()
        for its in seqs:
            rid = f"{m.name}-{cond}-{mode}-{its[0]['sequence_id']}"
            write_jsonl(out, run_sequence(its, m, cond, backend, rid, mode=mode, testbed=TB), append=True)
        print(f"  {m.name:9} {cond} {mode:12} done in {time.time()-t1:5.1f}s")
print(f"total {time.time()-t0:.1f}s -> {out}")
json.dump(metas, open(f"{ROOT}/results/{PFX}sequence_meta.json", "w"), indent=1, sort_keys=True)
