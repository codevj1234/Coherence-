"""Run on a machine with Docker + pybatfish.  Compares real Batfish with the reference engine.

    docker run --rm -d -p 8888:8888 -p 9997:9997 -p 9996:9996 batfish/allinone
    pip install pybatfish
    python scripts/validate_against_batfish.py

Prints, for the baseline and each of the 30 oracle cases, the broken-invariant set according to
(a) the human expectation, (b) the reference engine, (c) Batfish.  Exit code 0 only if all three agree.
Paste the final summary line into Review 2 as the Batfish validation result.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from cuc.batfish_backend import BatfishBackend
from cuc.invariants import ReferenceBackend, baseline_invariants
from cuc.oracle import build_cases
from cuc.topology import build_baseline

bf, ref = BatfishBackend(), ReferenceBackend()
base, invs = build_baseline(), baseline_invariants()
broken = lambda r: sorted(i for i, x in r.items() if not x.passed)

b0 = broken(bf.check(base, invs))
print(f"baseline: batfish broken={b0}  (expected [])")
ok = not b0
agree = 0
for c in build_cases():
    cand = c.edit(base)
    exp, r, b = sorted(c.expected_broken), broken(ref.check(cand, invs)), None
    try:
        b = broken(bf.check(cand, invs))
    except Exception as e:
        b = f"ERROR {e}"
    good = exp == r == b
    agree += good
    print(f"{c.id} {'OK ' if good else 'DIFF'} human={exp} reference={r} batfish={b}")
print(f"\nBatfish validation: {agree}/30 cases where human, reference engine and Batfish all agree")
sys.exit(0 if ok and agree == 30 else 1)
