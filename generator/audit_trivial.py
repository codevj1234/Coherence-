"""Audit: which benchmark steps can a model pass by changing nothing?

A step is *trivial* when the invariant it adds already holds BEFORE its gold patch is applied: a model that returns
the configs unchanged then gets new_intent_ok = True for that step. A source is *unlocated* when no interface
owns the invariant's source IP, so the flow never enters the network and an isolation holds whatever the configs
say (BatfishOracle reports the same in `last_unlocated`).

    python -m generator.audit_trivial                  # all frozen test sequences
    python -m generator.audit_trivial --split train
    python -m generator.audit_trivial --strict         # exit code 1 if any step is trivial (for CI)

Frozen sequences are only read, never changed. Making these steps non-trivial means changing the generator and
regenerating the frozen data (FROZEN.json hashes, SFT export), which is a team decision.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import sys
from collections import Counter
from pathlib import Path

from agent.contract.io import read_jsonl
from agent.contract.models import Intent, Invariant
from generator.oracle.reference import ReferenceOracle, parse_device_config
from generator.topology.catalog import get_topology
from generator.topology.generator import generate_topology_configs

DATA = Path(__file__).resolve().parent / "data"


def _located(configs: dict[str, str], ip: ipaddress.IPv4Address) -> bool:
    for name, cfg in configs.items():
        for intf in parse_device_config(name, cfg).interfaces.values():
            if intf.network is not None and ip in intf.network:
                return True
    return False


def audit_sequence(manifest_path: Path, oracle: ReferenceOracle | None = None) -> list[dict]:
    """One row per step: type, trap or regular, trivial (already holds before the edit), source located."""
    oracle = oracle or ReferenceOracle()
    man = json.loads(manifest_path.read_text())
    spec = get_topology(man["topology_id"])
    configs = generate_topology_configs(spec)
    rows = []
    for step, intent in enumerate(read_jsonl(manifest_path.with_name(man["sequence_id"] + ".jsonl"), Intent)):
        inv = Invariant.model_validate(man["invariants_added"][str(step)])
        rows.append(dict(
            sequence_id=man["sequence_id"], step=step, type=str(intent.type),
            trap=str(step) in man["traps"],
            trivial=oracle.check(configs, [inv])[inv.id],
            source_located=_located(configs, oracle._resolve_ip(inv.source)),
        ))
        configs = {**configs, **(intent.gold_patch or {})}
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="test", choices=["test", "train"])
    ap.add_argument("--strict", action="store_true", help="exit with code 1 if any step is trivial")
    a = ap.parse_args()

    oracle = ReferenceOracle()
    rows = [r for m in sorted((DATA / a.split).glob("*.manifest.json")) for r in audit_sequence(m, oracle)]
    total, trivial, unlocated = Counter(), Counter(), Counter()
    for r in rows:
        key = (r["type"], "trap" if r["trap"] else "regular")
        total[key] += 1
        trivial[key] += r["trivial"]
        unlocated[key] += not r["source_located"]

    seqs = len({r["sequence_id"] for r in rows})
    print(f"{a.split}: {seqs} sequences, {len(rows)} steps\n")
    print(f"{'intent type':<15}{'kind':<9}{'steps':>6}{'already true before the edit':>31}{'source outside network':>25}")
    for key in sorted(total):
        print(f"{key[0]:<15}{key[1]:<9}{total[key]:>6}{trivial[key]:>23} ({trivial[key] / total[key]:4.0%})"
              f"{unlocated[key]:>18} ({unlocated[key] / total[key]:4.0%})")
    n_triv = sum(trivial.values())
    print(f"\nA model that changes nothing satisfies the new intent on {n_triv} of {len(rows)} steps "
          f"({n_triv / len(rows):.0%}).")
    if a.strict and n_triv:
        sys.exit(1)


if __name__ == "__main__":
    main()
