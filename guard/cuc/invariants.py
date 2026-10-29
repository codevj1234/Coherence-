"""Invariant records, baseline set, and the checker interface.

The checker interface is the seam between backends:  check(texts, invariants)
-> {inv_id: InvResult}.  ReferenceBackend (this repo's Python engine) and
BatfishBackend (batfish_backend.py) both implement it.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Set, Tuple

from .engine import Flow, PROBES, host_ip, load_network
from .iosmodel import ParseIssue


@dataclass
class Invariant:
    id: str
    kind: str                       # reach | isolate | waypoint
    source: str                     # subnet, e.g. 10.1.1.0/24
    destination: str
    headers: Dict                   # {"protocol": "tcp", "ports": [443]}  or {"protocol": "ip"} = all probes
    via: Optional[str] = None       # waypoint device
    created_at_step: int = 0

    def flows(self) -> List[Flow]:
        s, d = host_ip(self.source), host_ip(self.destination)
        if self.headers.get("protocol") == "ip":
            return [Flow(s, d, p, port) for p, port in PROBES]
        return [Flow(s, d, self.headers["protocol"], p) for p in self.headers["ports"]]

    def describe(self) -> str:
        h = self.headers
        what = "any probe" if h["protocol"] == "ip" else f'{h["protocol"]}/{",".join(map(str, h["ports"]))}'
        base = f"{self.source} -> {self.destination} {what}"
        return {"reach": f"{base} must be reachable",
                "isolate": f"{base} must be blocked",
                "waypoint": f"{base} must pass through {self.via}"}[self.kind]


@dataclass
class InvResult:
    passed: bool
    detail: str = ""
    example_flow: Optional[Flow] = None
    deps: Set[Tuple] = field(default_factory=set)
    vacuous: bool = False           # isolation 'passed' only because there was no route


def _ecmp_deps(net, inv) -> set:
    """Every device on every equal-cost path of the invariant's flows (Batfish traceroute reports all of them)."""
    return {(d, "route", "*", None) for f in inv.flows() for p in net.ecmp_paths(f) for d in p}


def check_with_network(net, invs: List[Invariant]) -> Dict[str, InvResult]:
    res: Dict[str, InvResult] = {}
    for inv in invs:
        traces = [(f, net.trace(f)) for f in inv.flows()]
        if inv.kind == "reach":
            bad = next(((f, t) for f, t in traces if t.disposition != "ACCEPTED"), None)
            deps = set().union(*[t.deps for _, t in traces]) | _ecmp_deps(net, inv)
            if bad:
                f, t = bad
                where = f"denied at {t.deny[0]}:{t.deny[1]} {t.deny[2]} ACL {t.deny[3]} line {t.deny[4]}" \
                    if t.deny else t.disposition
                res[inv.id] = InvResult(False, f"{f.proto}/{f.dport} {where}", f, deps)
            else:
                res[inv.id] = InvResult(True, "", None, deps)
        elif inv.kind == "isolate":
            leak = next(((f, t) for f, t in traces if t.disposition == "ACCEPTED"), None)
            if leak:
                f, t = leak
                res[inv.id] = InvResult(False, f"leak: {f.proto}/{f.dport} is accepted via {'>'.join(t.path)}",
                                        f, set().union(*[tt.deps for _, tt in traces]))
            else:
                # Dependency = the denying ACL lines (not the routing: removing a route
                # would be a *vacuous* isolation, which we flag).
                deps = {x for _, t in traces for x in t.deps if x[1] in ("acl", "prefixlist")}
                # isolation that holds because routing drops the flow (route filter, VRF) depends on the device
                # where it is dropped: without this, undoing a VRF looked unrelated to the invariant (5 misses
                # found by scripts/validate_depmap_batfish.py before this line was added)
                deps |= {(t.path[-1], "route", "*", None) for _, t in traces if t.disposition == "NO_ROUTE" and t.path}
                vac = any(t.disposition == "NO_ROUTE" for _, t in traces)
                res[inv.id] = InvResult(True, "vacuous (no route)" if vac else "", None, deps, vac)
        elif inv.kind == "waypoint":
            # reachable, and no equal-cost path avoids the waypoint (Batfish: no successful path with via forbidden)
            def avoids(f):
                return any(inv.via not in p for p in net.ecmp_paths(f))
            bad = next(((f, t) for f, t in traces if t.disposition != "ACCEPTED" or inv.via not in t.path or avoids(f)), None)
            deps = set().union(*[t.deps for _, t in traces]) | _ecmp_deps(net, inv)
            if bad:
                f, t = bad
                res[inv.id] = InvResult(False, f"{t.disposition}, path {'>'.join(t.path)} (needs {inv.via})", f, deps)
            else:
                res[inv.id] = InvResult(True, "", None, deps)
    return res


class ReferenceBackend:
    name = "reference"

    def check(self, texts: Dict[str, str], invs: List[Invariant]) -> Dict[str, InvResult]:
        return check_with_network(load_network(texts), invs)   # raises ParseIssue on bad configs


def baseline_invariants() -> List[Invariant]:
    S = {k: f"10.1.{k}.0/24" for k in (1, 2, 3, 4)}
    T = lambda *p: {"protocol": "tcp", "ports": list(p)}
    return [
        Invariant("R01", "reach", S[3], S[1], T(22)),
        Invariant("R02", "reach", S[2], S[1], T(80)),
        Invariant("R03", "reach", S[1], S[2], T(80)),
        Invariant("R04", "reach", S[1], S[3], T(443)),
        Invariant("R05", "reach", S[2], S[3], T(443)),
        Invariant("R06", "reach", S[1], S[3], T(80)),
        Invariant("R07", "reach", S[1], S[4], T(80)),
        Invariant("R08", "reach", S[3], S[4], T(443)),
        Invariant("I01", "isolate", S[2], S[3], T(22)),
        Invariant("I02", "isolate", S[4], S[3], {"protocol": "ip"}),
        Invariant("I03", "isolate", S[2], S[1], T(22)),
        Invariant("I04", "isolate", S[3], S[2], T(80)),
        Invariant("I05", "isolate", S[2], S[4], T(80)),
        Invariant("I06", "isolate", S[4], S[1], T(22)),
        Invariant("W01", "waypoint", S[1], S[4], T(80), via="FW"),
    ]
