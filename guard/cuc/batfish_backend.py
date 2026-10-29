"""Batfish backend: same interface as ReferenceBackend, but the judge is real Batfish (pybatfish).

STATUS: check() validated against real Batfish (30/30 oracle cases on the 9-device bed; Topology Zoo beds:
see scripts/validate_zoo_*.py).  deps() builds the dependency map from Batfish traceroute; it is checked
by scripts/validate_depmap_batfish.py.  This is the only file that touches pybatfish.

    docker run --rm -p 8888:8888 -p 9997:9997 -p 9996:9996 batfish/allinone
    pip install pybatfish
"""
from __future__ import annotations

import os
import shutil
import tempfile
from typing import Dict, List

from .engine import Flow
from .invariants import Invariant, InvResult
from .iosmodel import ParseIssue, parse_config


class BatfishBackend:
    name = "batfish"

    def __init__(self, host: str = "localhost", network: str = "cuc"):
        from pybatfish.client.session import Session            # imported lazily: optional dependency
        from pybatfish.datamodel.flow import HeaderConstraints, PathConstraints
        self._HC, self._PC = HeaderConstraints, PathConstraints
        self.bf = Session(host=host)
        self.bf.set_network(network)
        self.net = network
        self._shut = set()

    def _snapshot(self, texts: Dict[str, str]) -> None:
        d = tempfile.mkdtemp(prefix="cuc_bf_")
        try:
            os.makedirs(os.path.join(d, "configs"))
            for dev, t in texts.items():
                with open(os.path.join(d, "configs", f"{dev}.cfg"), "w") as f:
                    f.write(t)
            self.bf.init_snapshot(d, name="cand", overwrite=True)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        status = self.bf.q.fileParseStatus().answer().frame()
        bad = status[status["Status"] != "PASSED"]
        if len(bad):
            raise ParseIssue(f"Batfish parse status not PASSED for: {list(bad['File_Name'])}")
        undefined = self.bf.q.undefinedReferences().answer().frame()
        if len(undefined):
            raise ParseIssue(f"Batfish undefined references: {undefined.iloc[0].to_dict()}")

    def _host_ifaces(self, texts):
        out = {}
        for dev, t in texts.items():
            for ifn, itf in parse_config(t, dev).interfaces.items():
                if itf.ip is not None and itf.ip.network.prefixlen < 30:
                    out[str(itf.ip.network)] = (dev, ifn)
        return out

    def _shut_ifaces(self, texts):
        out = set()
        for dev, t in texts.items():
            for ifn, itf in parse_config(t, dev).interfaces.items():
                if itf.shutdown:
                    out.add((dev, ifn))
        return out

    def _reachable(self, flow: Flow, start, via=None, forbid=None) -> bool:
        dev, ifn = start
        if start in self._shut:
            # no traffic can enter through an administratively shut interface; Batfish cannot resolve such a
            # start location (the answer is an error table), so decide it here exactly as the reference engine does
            return False
        kw_pc = dict(startLocation=f"@enter({dev}[{ifn}])")
        if via:
            kw_pc["transitLocations"] = via
        if forbid:
            kw_pc["forbiddenLocations"] = forbid
        pc = self._PC(**kw_pc)
        kw = dict(srcIps=flow.src_ip, dstIps=flow.dst_ip, ipProtocols=[flow.proto.upper()])
        if flow.proto in ("tcp", "udp"):
            kw["dstPorts"] = str(flow.dport)
        ans = self.bf.q.reachability(pathConstraints=pc, headers=self._HC(**kw), actions="SUCCESS").answer().frame()
        return len(ans) > 0

    def check(self, texts: Dict[str, str], invs: List[Invariant]) -> Dict[str, InvResult]:
        self._snapshot(texts)
        hosts = self._host_ifaces(texts)
        self._shut = self._shut_ifaces(texts)
        res: Dict[str, InvResult] = {}
        for inv in invs:
            start = hosts[inv.source]
            flows = inv.flows()
            if inv.kind == "isolate":
                leak = next((f for f in flows if self._reachable(f, start)), None)
                res[inv.id] = InvResult(leak is None, "" if leak is None else f"leak {leak.proto}/{leak.dport}", leak)
            else:
                miss = next((f for f in flows if not self._reachable(f, start)), None)
                if miss is None and inv.kind == "waypoint":
                    # every path must cross the waypoint: no successful path may exist with it forbidden
                    bypass = next((f for f in flows if self._reachable(f, start, forbid=inv.via)), None)
                    res[inv.id] = InvResult(bypass is None, "" if bypass is None else f"path avoids {inv.via} {bypass.proto}/{bypass.dport}", bypass)
                else:
                    res[inv.id] = InvResult(miss is None, "" if miss is None else f"unreachable {miss.proto}/{miss.dport}", miss)
        return res


    # ------------------------------------------------------------------------------------------------
    # dependency map from Batfish traceroute (same tuple format as the reference engine's deps)
    # ------------------------------------------------------------------------------------------------
    def deps(self, texts: Dict[str, str], invs: List[Invariant]) -> Dict[str, set]:
        """{inv_id: {(device, 'route'|'acl', name, None)}} from traceroute of every flow of every invariant.
        reach / waypoint: every device on every trace (route) and every filter applied on the way (acl).
        isolate: the filters that DENIED the flow, plus the device where routing dropped it (route), which is
        where a prefix filter or VRF lives."""
        self._snapshot(texts)
        hosts = self._host_ifaces(texts)
        shut = self._shut_ifaces(texts)
        node_name = {d.lower(): d for d in texts}
        acl_name = {d: {a.lower(): a for a in parse_config(t, d).acls} for d, t in texts.items()}
        out: Dict[str, set] = {}
        for inv in invs:
            dev, ifn = hosts[inv.source]
            d = set()
            if (dev, ifn) in shut:
                d.add((dev, "route", "*", None))
                out[inv.id] = d
                continue
            for f in inv.flows():
                kw = dict(srcIps=f.src_ip, dstIps=f.dst_ip, ipProtocols=[f.proto.upper()])
                if f.proto in ("tcp", "udp"):
                    kw["dstPorts"] = str(f.dport)
                frame = self.bf.q.traceroute(startLocation=f"@enter({dev}[{ifn}])", headers=self._HC(**kw)).answer().frame()
                for traces in frame["Traces"]:
                    for tr in traces:
                        disp = str(getattr(tr, "disposition", ""))
                        hops = list(getattr(tr, "hops", []))
                        for k, hop in enumerate(hops):
                            raw = getattr(hop, "node", "")
                            node = raw if isinstance(raw, str) else getattr(raw, "hostname", str(raw))
                            node = node_name.get(node.lower(), node)
                            if inv.kind != "isolate":
                                d.add((node, "route", "*", None))
                            for st in getattr(hop, "steps", []):
                                flt = getattr(getattr(st, "detail", None), "filter", None)
                                if not flt:
                                    continue
                                name = acl_name.get(node, {}).get(str(flt).lower(), str(flt))
                                if inv.kind != "isolate" or "DENIED" in str(getattr(st, "action", "")).upper():
                                    d.add((node, "acl", name, None))
                            if inv.kind == "isolate" and k == len(hops) - 1 and "NO_ROUTE" in disp.upper():
                                d.add((node, "route", "*", None))
            out[inv.id] = d
        return out
