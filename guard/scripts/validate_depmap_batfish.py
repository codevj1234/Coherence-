"""Dependency-map check against Batfish (roadmap gap 4).

For every breaking mutation from validate_zoo_mutations.py, the guard's dependency map must name each invariant
the edit breaks: an invariant counts as 'affected' when its dependencies (devices / ACLs / route filters on its
traced flows, taken from the state BEFORE the edit) overlap the edit's footprint.  A broken invariant that is not
affected is a MISS.  The map is built twice - from the reference engine and from Batfish traceroute - and the
script reports misses for both and how often the two maps flag the same invariants.

    python3 scripts/validate_depmap_batfish.py [bed ...]          # needs Batfish running (slow: traceroutes)
    python3 scripts/validate_depmap_batfish.py --ref-only         # reference map only, no Batfish
"""
import os, sys, time
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from cuc.edits import diff_footprint
from cuc.harness import _overlaps
from cuc.invariants import ReferenceBackend
from cuc.testbed import STANDARD_BEDS, get_testbed
import validate_zoo_mutations as vm

REF_ONLY = "--ref-only" in sys.argv
beds = [a for a in sys.argv[1:] if a in STANDARD_BEDS] or list(STANDARD_BEDS)
ref = ReferenceBackend()
bf = None
if not REF_ONLY:
    from cuc.batfish_backend import BatfishBackend
    bf = BatfishBackend(network="cuc_depmap")

tot = dict(regressions=0, miss_ref=0, miss_bf=0, same_flags=0, compared=0)
for name in beds:
    tb = get_testbed(name)
    muts = vm.all_mutations(tb)
    prev_of, control = {}, {}
    for label, cand, invs in muts:                      # the state each mutation was made from
        group = label.split("]")[0] + "]" if label.startswith("[") else ""
        if "(control)" in label:
            control[group] = cand
        prev_of[label] = control.get(group, tb.baseline) if group else tb.baseline
    cache = {}
    print(f"[{name}] {len(muts)} mutations", flush=True)
    for label, cand, invs in muts:
        if "(control)" in label:
            continue
        prev = prev_of[label]
        key = id(prev), len(invs)
        if key not in cache:
            t0 = time.time()
            rres = ref.check(prev, invs)
            dbf_ = None
            if bf:
                try:
                    dbf_ = bf.deps(prev, invs)
                except Exception as e:                  # e.g. Batfish cannot parse this base state
                    print(f"  (Batfish map unavailable for this base state: {str(e)[:100]})", flush=True)
            cache[key] = ({i: r.deps for i, r in rres.items()}, dbf_)
            print(f"  (dependency maps for this base state built in {time.time()-t0:.0f}s)", flush=True)
        dref, dbf = cache[key]
        try:
            res = ref.check(cand, invs)
        except Exception:
            print(f"  skip {label}: does not parse")
            continue
        newly = sorted(i for i, r in res.items() if not r.passed)
        fp = diff_footprint(prev, cand)
        aff_ref = {i for i in dref if _overlaps(dref[i], fp)}
        m_ref = [i for i in newly if i not in aff_ref]
        line = f"  {label:46} broken={newly} miss(ref)={m_ref}"
        tot["regressions"] += len(newly); tot["miss_ref"] += len(m_ref)
        if dbf is not None:
            aff_bf = {i for i in dbf if _overlaps(dbf[i], fp)}
            m_bf = [i for i in newly if i not in aff_bf]
            same = aff_ref == aff_bf
            tot["miss_bf"] += len(m_bf); tot["same_flags"] += same; tot["compared"] += 1
            line += f" miss(batfish)={m_bf} {'same-flags' if same else 'flags-differ ref-only=' + str(sorted(aff_ref - aff_bf)) + ' bf-only=' + str(sorted(aff_bf - aff_ref))}"
        print(line, flush=True)
print(f"\nDependency map: {tot['regressions']} broken invariants across all mutations; "
      f"missed by reference map: {tot['miss_ref']}" + ("" if REF_ONLY else
      f"; missed by Batfish map: {tot['miss_bf']}; identical affected sets on {tot['same_flags']}/{tot['compared']} mutations"))
