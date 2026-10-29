"""Does Batfish agree with ReferenceOracle on the frozen benchmark?  Needs a running Batfish container.

For each chosen frozen test sequence:
1. The sequence is regenerated from its manifest seed (and checked to be identical to the frozen file), which also
   recovers the trap steps' naive edits, since the files keep only the gold patches.
2. Gold replay: from the baseline, gold patches are applied step by step; every `--every` steps, all invariants
   active so far are checked by both oracles.
3. Trap proofs: for `--traps` trap steps, the naive edit is applied to the state before the step; the reference
   oracle says it breaks `broken_invariant_id`. Both oracles check every invariant active before that step.

Each invariant verdict is compared. DIFF lines show both oracles' explanations.

    docker start batfish        # or: docker run -d --name batfish -p 8888:8888 -p 9997:9997 -p 9996:9996 batfish/allinone
    python -m generator.oracle.validate_batfish                       # 2 sequences (one small, one large topology)
    python -m generator.oracle.validate_batfish --seqs 4 --every 5 --traps 6
"""

from __future__ import annotations

import argparse
import json
import random
import time
from collections import Counter
from pathlib import Path

from agent.contract.io import read_jsonl
from agent.contract.models import Intent, Invariant
from generator.oracle.batfish import BatfishOracle
from generator.oracle.reference import ReferenceOracle
from generator.sequences import builder as builder_mod
from generator.topology.catalog import get_topology
from generator.topology.generator import generate_topology_configs

DATA = Path(__file__).resolve().parents[1] / "data"


def regenerate_with_traps(manifest: dict):
    """Rebuild the sequence from its seed, keeping each trap step's naive edit and the state before it."""
    captured: dict[int, tuple[dict, object]] = {}
    real = builder_mod.generate_trap_step

    def spy(**kw):
        res = real(**kw)
        if res is not None:
            captured[kw["step_idx"]] = (dict(kw["current_configs"]), res)
        return res

    builder_mod.generate_trap_step = spy
    try:
        seq = builder_mod.build_sequence(get_topology(manifest["topology_id"]), length=manifest["length"],
                                         seed=manifest["seed"], sequence_id=manifest["sequence_id"],
                                         split=manifest["split"])
    finally:
        builder_mod.generate_trap_step = real
    traps = {int(k): captured[int(k)] for k in manifest["traps"]}
    return seq, traps


def compare(label, configs, invs, ref, bf, tally, diffs):
    t0 = time.time()
    r = ref.check(configs, invs)
    b = bf.check(configs, invs)
    bad = [i for i in invs if r[i.id] != b[i.id]]
    tally["invariants"] += len(invs)
    tally["agree"] += len(invs) - len(bad)
    tally["unlocated"] += len(bf.last_unlocated)
    for name, status in bf.last_parse_status.items():
        tally[f"parse:{status}"] += 1
    print(f"  {'OK  ' if not bad else 'DIFF'} {label}: {len(invs) - len(bad)}/{len(invs)} agree"
          f"  ({time.time() - t0:.0f}s)", flush=True)
    for inv in bad:
        er, eb = ref.explain_failure(configs, inv), bf.explain_failure(configs, inv)
        line = (f"       {inv.id} [{inv.kind} {inv.source} -> {inv.destination} {inv.headers}"
                f"{' via ' + inv.via if inv.via else ''}]\n"
                f"         reference={r[inv.id]}: {er.reason}\n"
                f"         batfish  ={b[inv.id]}: {eb.reason}")
        print(line, flush=True)
        diffs.append(line)
    return r, b


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seqs", type=int, default=2, help="frozen test sequences to check (spread over topologies)")
    ap.add_argument("--every", type=int, default=10, help="compare after every N-th gold step")
    ap.add_argument("--traps", type=int, default=4, help="trap steps per sequence whose naive edit is checked")
    ap.add_argument("--host", default="localhost")
    a = ap.parse_args()

    bf = BatfishOracle(host=a.host)
    if not bf.is_available:
        raise SystemExit("Batfish is not reachable: start the container (docker start batfish) and pip install pybatfish")
    ref = ReferenceOracle()

    manifests = sorted(DATA.glob("test/*.manifest.json"))
    by_topo: dict[str, list[Path]] = {}
    for m in manifests:
        by_topo.setdefault(json.loads(m.read_text())["topology_id"], []).append(m)
    chosen, k = [], 0
    while len(chosen) < a.seqs and any(by_topo.values()):        # round-robin over topologies
        topo = sorted(by_topo)[k % len(by_topo)]
        if by_topo[topo]:
            chosen.append(by_topo[topo].pop(0))
        k += 1

    tally: Counter = Counter()
    diffs: list[str] = []
    trap_proofs = Counter()
    for mpath in chosen:
        manifest = json.loads(mpath.read_text())
        sid = manifest["sequence_id"]
        print(f"{sid} ({manifest['topology_id']}, {manifest['length']} steps, {manifest['trap_count']} traps)", flush=True)
        seq, traps = regenerate_with_traps(manifest)
        frozen = list(read_jsonl(mpath.with_name(sid + ".jsonl"), Intent))
        same = [x.gold_patch for x in frozen] == [x.gold_patch for x in seq.intents]
        if not same:
            print("  regenerated sequence differs from the frozen file: trap proofs skipped for it")
        spec = get_topology(manifest["topology_id"])
        configs = generate_topology_configs(spec)
        invs = list(spec.baseline_invariants)
        compare("baseline", configs, invs, ref, bf, tally, diffs)
        added = {int(k): Invariant.model_validate(v) for k, v in manifest["invariants_added"].items()}
        for step, intent in enumerate(frozen):
            configs = {**configs, **(intent.gold_patch or {})}
            invs = invs + [added[step]]
            if (step + 1) % a.every == 0 or step == len(frozen) - 1:
                compare(f"after gold step {step}", configs, invs, ref, bf, tally, diffs)
        if same:
            picks = sorted(traps)
            random.Random(0).shuffle(picks)
            for step in sorted(picks[: a.traps]):
                before, res = traps[step]
                active = list(spec.baseline_invariants) + [added[s] for s in range(step)]
                r, b = compare(f"trap step {step} ({res.trap_kind}), naive edit", {**before, **res.naive_edit},
                               active, ref, bf, tally, diffs)
                broken = res.broken_invariant_id
                trap_proofs["checked"] += 1
                trap_proofs["reference says broken"] += r.get(broken) is False
                trap_proofs["batfish says broken"] += b.get(broken) is False

    print(f"\nInvariant verdicts, Batfish vs ReferenceOracle: {tally['agree']}/{tally['invariants']} agree")
    print(f"Trap proofs (naive edit breaks the earlier invariant): reference {trap_proofs['reference says broken']}"
          f"/{trap_proofs['checked']}, Batfish {trap_proofs['batfish says broken']}/{trap_proofs['checked']}")
    print(f"Verdicts on flows whose source no interface owns (isolation holds only vacuously): {tally['unlocated']}")
    parse = {k[6:]: v for k, v in tally.items() if k.startswith("parse:")}
    print(f"Config files Batfish did not fully parse, summed over snapshots: {parse or 'none'}")


if __name__ == "__main__":
    main()
