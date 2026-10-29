"""Run on a machine with the Batfish container + pybatfish (same setup as validate_against_batfish.py).

For each standard testbed it checks that Batfish and the reference engine agree on
  (a) the baseline invariants, and
  (b) a sample of candidate configurations: for the first N steps of a generated sequence, the gold edit,
      the over-broad edit and the misplaced edit, each checked against ALL tracked invariants.
Usage:  python3 scripts/validate_zoo_batfish.py [N_STEPS=6] [bed ...] [acl|pf|wp|vrf ...]   (restrict intent types)
Exit code 0 only if every comparison agrees.  Paste the output back; DIFF lines name the invariant sets."""
import os, sys, time
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from cuc.batfish_backend import BatfishBackend
from cuc.intents import apply_intent, generate_sequence, make_invariant
from cuc.invariants import Invariant, ReferenceBackend
from cuc.testbed import STANDARD_BEDS, get_testbed

args = sys.argv[1:]
N = int(args[0]) if args and args[0].isdigit() else 6
beds = [a for a in args if a in STANDARD_BEDS] or list(STANDARD_BEDS)
TOKENS = {"acl": "acl", "pf": "prefix_filter", "wp": "waypoint", "vrf": "vrf_isolate"}
ONLY = tuple(TOKENS[a] for a in args if a in TOKENS) or None    # e.g. 'wp' = only waypoint intents
ref, bf = ReferenceBackend(), BatfishBackend(network="cuc_zoo")
broken = lambda r: sorted(i for i, x in r.items() if not x.passed)
total = agree = 0
for name in beds:
    tb = get_testbed(name)
    t0 = time.time()
    b_ref, b_bf = broken(ref.check(tb.baseline, tb.invariants)), broken(bf.check(tb.baseline, tb.invariants))
    total += 1; agree += (b_ref == b_bf == [])
    print(f"[{name}] baseline: reference={b_ref} batfish={b_bf} ({time.time()-t0:.0f}s)", flush=True)
    its, _ = generate_sequence(11, N, ref, testbed=tb, intent_types=ONLY)
    state, tracked = dict(tb.baseline), list(tb.invariants)
    for it in its:
        p, inv = it["params"], Invariant(**it["invariant"])
        for label, kw in (("gold", {}), ("broad", dict(broad=True)), ("misplaced", dict(misplaced=True))):
            cand = apply_intent(state, p, **kw)
            invs = tracked + [inv]
            r, b = broken(ref.check(cand, invs)), None
            try:
                b = broken(bf.check(cand, invs))
            except Exception as e:
                b = f"ERROR {e}"
            ok = r == b
            total += 1; agree += ok
            print(f"[{name}] step {it['step']} {label:9} {'OK  ' if ok else 'DIFF'} reference={r} batfish={b}", flush=True)
        state = apply_intent(state, p)
        tracked.append(inv)
print(f"\nZoo validation: {agree}/{total} comparisons where reference engine and Batfish agree")
sys.exit(0 if agree == total else 1)
