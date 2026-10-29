"""Batfish verification oracle adapter.

Implements the same `Oracle` interface as `ReferenceOracle`, with real Batfish as the judge. The query logic
is ported from the Track A checker (`guard/cuc/batfish_backend.py` on branch `feat/track-a-guard`), which was
validated against Batfish on 30 oracle cases, 162 Topology Zoo comparisons and 106 broken invariants; it is
adapted here to this branch's contract (`Invariant` with host or CIDR endpoints, `headers.dst_port`, `via`).

Semantics, kept identical to `ReferenceOracle` so the two oracles can be compared one for one:
- One flow per invariant: source and destination are host IPs, or a CIDR resolved to its 10th host
  (`ReferenceOracle._resolve_ip`); protocol and `dst_port` come from `headers`.
- The flow enters the network at the interface whose subnet contains the source IP (`@enter(device[iface])`),
  so that interface's inbound ACL applies, exactly as in the reference simulation.
- A flow is delivered when every Batfish trace ends ACCEPTED or DELIVERED_TO_SUBNET.
  reach: delivered.  isolate: no trace delivered.  waypoint: delivered and every trace crosses `via`.
- Protocol "ip" is sent as a GRE packet: like the reference oracle's "ip" packet, it matches only `ip` ACL
  lines, never `tcp`/`udp`/`icmp` lines.
- A source IP that no interface owns has no entry point: reach/waypoint fail, isolate passes (the reference
  oracle does the same). `explain_failure` says so, and `last_unlocated` lists such invariants, because an
  isolation that holds only for this reason holds vacuously.

Batfish reports parse problems instead of failing on them; after every snapshot the files it did not fully
parse are kept in `last_parse_status` (e.g. `rd` under `ip vrf` is flagged PARTIALLY_UNRECOGNIZED by Batfish).

When pybatfish is missing or Batfish is unreachable, `is_available` is False and every query raises
`BatfishNotAvailableError`, so test suites can skip cleanly. Start Batfish with
    docker run -d --name batfish -p 8888:8888 -p 9997:9997 -p 9996:9996 batfish/allinone
"""

from __future__ import annotations

import ipaddress
import os
import shutil
import tempfile

from agent.contract.models import Invariant, InvariantKind
from generator.oracle.base import FailureDetail, Oracle
from generator.oracle.reference import ReferenceOracle, parse_device_config

DELIVERED = {"ACCEPTED", "DELIVERED_TO_SUBNET"}
IP_PROBE_PROTOCOL = "GRE"


class BatfishNotAvailableError(RuntimeError):
    """Raised when Batfish verification is invoked but Batfish is unavailable."""


class BatfishOracle(Oracle):
    """Adapter wrapping Pybatfish verification queries."""

    def __init__(self, host: str = "localhost", snapshot_dir: str | None = None, network: str = "coherence") -> None:
        self.host = host
        self.snapshot_dir = snapshot_dir          # optional: keep each snapshot's configs here for inspection
        self.network = network
        self._available = False
        self._client = None
        self._hc = None
        self._snap_key: tuple | None = None
        self._models: dict = {}
        self._ref = ReferenceOracle()
        self.last_parse_status: dict[str, str] = {}
        self.last_unlocated: list[str] = []
        self._check_availability()

    def _check_availability(self) -> None:
        """Probe for pybatfish module and active service reachability."""
        try:
            from pybatfish.client.session import Session  # type: ignore[import-not-found]
            from pybatfish.datamodel.flow import HeaderConstraints  # type: ignore[import-not-found]

            session = Session(host=self.host)
            session.set_network(self.network)   # a real round trip to the service
            self._client = session
            self._hc = HeaderConstraints
            self._available = True
        except Exception:
            self._available = False
            self._client = None

    @property
    def is_available(self) -> bool:
        """True if Pybatfish is installed and the Batfish service responds."""
        return self._available

    # ------------------------------------------------------------------ snapshot and flow helpers

    def _require(self) -> None:
        if not self.is_available:
            raise BatfishNotAvailableError(
                "Batfish service is unavailable. Ensure pybatfish is installed "
                "and Batfish Docker container is running."
            )

    def _load(self, configs: dict[str, str]) -> None:
        """Upload the configs as the current snapshot (skipped when they are unchanged since the last call)."""
        key = tuple(sorted(configs.items()))
        if key == self._snap_key:
            return
        d = tempfile.mkdtemp(prefix="coherence_bf_")
        try:
            os.makedirs(os.path.join(d, "configs"))
            for dev, text in configs.items():
                with open(os.path.join(d, "configs", f"{dev}.cfg"), "w", encoding="utf-8") as f:
                    f.write(text)
            if self.snapshot_dir:
                keep = os.path.join(self.snapshot_dir, "configs")
                shutil.rmtree(keep, ignore_errors=True)
                shutil.copytree(os.path.join(d, "configs"), keep)
            self._client.init_snapshot(d, name="cand", overwrite=True)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        status = self._client.q.fileParseStatus().answer().frame()
        self.last_parse_status = {
            os.path.basename(str(r.File_Name)): str(r.Status) for r in status.itertuples() if str(r.Status) != "PASSED"
        }
        self._models = {name: parse_device_config(name, cfg) for name, cfg in configs.items()}
        self._snap_key = key

    def _start(self, src_ip: ipaddress.IPv4Address) -> tuple[str, str] | None:
        """(device, interface) whose subnet contains the source IP, in the same order the reference oracle uses."""
        for dev in self._models.values():
            for intf in dev.interfaces.values():
                if intf.network is not None and src_ip in intf.network:
                    return dev.name, intf.name
        return None

    def _headers(self, inv: Invariant, src_ip, dst_ip):
        proto = str(inv.headers.get("protocol", "tcp")).lower()
        kw = dict(srcIps=str(src_ip), dstIps=str(dst_ip),
                  ipProtocols=[IP_PROBE_PROTOCOL if proto == "ip" else proto.upper()])
        port = inv.headers.get("dst_port")
        if proto in ("tcp", "udp") and port is not None:
            kw["dstPorts"] = str(port)
        return self._hc(**kw)

    def _traces(self, inv: Invariant):
        """Batfish traces of the invariant's flow, or None when the source has no entry interface."""
        src_ip = self._ref._resolve_ip(inv.source)
        dst_ip = self._ref._resolve_ip(inv.destination)
        start = self._start(src_ip)
        if start is None:
            return None, None, src_ip, dst_ip
        dev, ifn = start
        frame = self._client.q.traceroute(
            startLocation=f"@enter({dev}[{ifn}])", headers=self._headers(inv, src_ip, dst_ip)
        ).answer().frame()
        traces = [tr for row in frame["Traces"] for tr in row]
        return traces, start, src_ip, dst_ip

    @staticmethod
    def _disposition(tr) -> str:
        return str(getattr(tr, "disposition", "")).upper()

    def _nodes(self, tr) -> list[str]:
        names = {n.lower(): n for n in self._models}
        out = []
        for hop in getattr(tr, "hops", []):
            raw = getattr(hop, "node", "")
            node = raw if isinstance(raw, str) else getattr(raw, "hostname", str(raw))
            out.append(names.get(node.lower(), node))
        return out

    def _verdict(self, inv: Invariant, traces) -> bool:
        if traces is None:                                   # no entry point: nothing can be delivered
            return inv.kind == InvariantKind.ISOLATE
        delivered = [self._disposition(t) in DELIVERED for t in traces]
        if inv.kind == InvariantKind.ISOLATE:
            return not any(delivered)
        if not traces or not all(delivered):
            return False
        if inv.kind == InvariantKind.WAYPOINT:
            via = inv.via or inv.headers.get("waypoint")
            return all(via in self._nodes(t) for t in traces)
        return True

    # ------------------------------------------------------------------ Oracle interface

    def check(self, configs: dict[str, str], invariants: list[Invariant]) -> dict[str, bool]:
        """Execute Batfish traceroute for every invariant; True means the invariant holds."""
        self._require()
        if not invariants:
            return {}
        self._load(configs)
        results: dict[str, bool] = {}
        self.last_unlocated = []
        for inv in invariants:
            traces, start, _, _ = self._traces(inv)
            if start is None:
                self.last_unlocated.append(inv.id)
            results[inv.id] = self._verdict(inv, traces)
        return results

    def explain_failure(self, configs: dict[str, str], invariant: Invariant) -> FailureDetail:
        """Provide Batfish-derived failure diagnosis: where the flow was denied, dropped or misrouted."""
        self._require()
        self._load(configs)
        traces, start, src_ip, dst_ip = self._traces(invariant)
        passed = self._verdict(invariant, traces)
        if start is None:
            return FailureDetail(invariant.id, passed,
                                 reason=f"Source IP {src_ip} not found in any device interface subnet.")
        if passed:
            return FailureDetail(invariant.id, True, reason="Invariant holds in Batfish.")
        if invariant.kind == InvariantKind.ISOLATE:
            tr = next(t for t in traces if self._disposition(t) in DELIVERED)
            return FailureDetail(invariant.id, False, reason=(
                f"Traffic was delivered successfully, violating isolation policy. "
                f"Path: {' -> '.join(self._nodes(tr))}."))
        if not traces:
            return FailureDetail(invariant.id, False, reason="Batfish returned no trace for the flow.")
        bad = next((t for t in traces if self._disposition(t) not in DELIVERED), None)
        if bad is None:                                      # delivered everywhere, but a path skips the waypoint
            via = invariant.via or invariant.headers.get("waypoint")
            tr = next(t for t in traces if via not in self._nodes(t))
            return FailureDetail(invariant.id, False, reason=(
                f"Packet delivered but failed to traverse required waypoint '{via}'. "
                f"Visited path: {' -> '.join(self._nodes(tr))}."))
        nodes = self._nodes(bad)
        disp = self._disposition(bad)
        for hop, node in zip(getattr(bad, "hops", []), nodes):
            for st in getattr(hop, "steps", []):
                if "DENIED" not in str(getattr(st, "action", "")).upper():
                    continue
                acl = getattr(getattr(st, "detail", None), "filter", None)
                acl = self._acl_name(node, acl)
                line, text = self._denying_line(invariant, src_ip, dst_ip, node, acl)
                where = f"line {line}" if line else "implicit deny"
                return FailureDetail(invariant.id, False, blocking_device=node, blocking_acl=acl,
                                     blocking_line=line, rule_text=text,
                                     reason=f"ACL '{acl}' on {node} denied traffic ({where}); disposition {disp}.")
        last = nodes[-1] if nodes else None
        return FailureDetail(invariant.id, False, blocking_device=last,
                             reason=f"Disposition {disp} at '{last}'. Path: {' -> '.join(nodes)}.")

    # ------------------------------------------------------------------ ACL line lookup

    def _acl_name(self, node: str, acl) -> str | None:
        if acl is None:
            return None
        model = self._models.get(node)
        names = {a.lower(): a for a in (model.acls if model else {})}
        return names.get(str(acl).lower(), str(acl))

    def _denying_line(self, inv, src_ip, dst_ip, node, acl) -> tuple[int | None, str | None]:
        """(line number in the ACL as ReferenceOracle counts it, line text) of the rule Batfish says matched."""
        model = self._models.get(node)
        if acl is None or model is None or acl not in model.acls:
            return None, None
        try:
            frame = self._client.q.testFilters(
                headers=self._headers(inv, src_ip, dst_ip), nodes=node, filters=acl
            ).answer().frame()
            content = " ".join(str(frame.iloc[0]["Line_Content"]).split()).lower() if len(frame) else ""
        except Exception:
            return None, None
        for rule in model.acls[acl]:
            if " ".join(rule.raw_text.split()).lower() == content:
                return rule.line_no, rule.raw_text
        return None, None                                    # no line matched: implicit deny
