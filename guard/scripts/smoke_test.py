"""Phase 3 smoke test: gold run must be 100% clean; deliberately corrupted patches must all be flagged."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cuc.harness import run_sequence, write_jsonl
from cuc.intents import generate_sequence
from cuc.invariants import ReferenceBackend
from cuc.models import CorruptedGoldModel, GoldModel

backend = ReferenceBackend()
intents, meta = generate_sequence(seed=7, length=20, backend=backend)
print("sequence:", meta)

gold = run_sequence(intents, GoldModel(), "C0", backend, "smoke-gold")
clean = all(r["outcome"] == "SUCCESS" and not r["broken_invariants"] and r["new_intent_ok"] for r in gold)
print(f"GOLD RUN: {sum(r['outcome']=='SUCCESS' for r in gold)}/20 steps SUCCESS, "
      f"final violated={gold[-1]['violated_now']}  -> {'CLEAN' if clean else 'NOT CLEAN'}")

corrupt = [3, 6, 9, 12, 15, 18]
bad = run_sequence(intents, CorruptedGoldModel(corrupt), "C0", backend, "smoke-corrupt")
flagged = [r["step"] for r in bad if r["outcome"] != "SUCCESS"]
print(f"CORRUPTED RUN: corrupted steps {corrupt}")
for r in bad:
    if r["step"] in corrupt:
        print(f"   step {r['step']:2d} -> {r['outcome']:13} broken={r['broken_invariants']}")
caught = set(corrupt) <= set(flagged)
print(f"all corrupted steps flagged: {caught}")
os.makedirs(os.path.join(os.path.dirname(__file__), "..", "results"), exist_ok=True)
write_jsonl(os.path.join(os.path.dirname(__file__), "..", "results", "smoke_test.jsonl"), gold + bad)
sys.exit(0 if clean and caught else 1)
