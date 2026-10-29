"""Run the 30-case oracle suite against a backend and print an agreement table."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from cuc.invariants import ReferenceBackend, baseline_invariants
from cuc.oracle import build_cases
from cuc.topology import build_baseline


def run(backend, quiet=False):
    base, invs = build_baseline(), baseline_invariants()
    assert all(r.passed for r in backend.check(base, invs).values()), "baseline must be clean"
    agree, rows = 0, []
    for c in build_cases():
        res = backend.check(c.edit(base), invs)
        got = {i for i, r in res.items() if not r.passed}
        ok = got == c.expected_broken
        agree += ok
        rows.append((c.id, c.label, ok, sorted(c.expected_broken), sorted(got)))
        if not quiet:
            print(f"{c.id} {c.label:8} {'AGREE' if ok else 'DISAGREE'}  expected={sorted(c.expected_broken)} got={sorted(got)}")
    print(f"\n{backend.name}: {agree}/{len(rows)} oracle cases agree with the human judgement")
    return agree, rows


if __name__ == "__main__":
    run(ReferenceBackend())
