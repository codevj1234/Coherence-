"""Reference data-plane engine (stand-in for Batfish when it cannot run).

Models exactly what the generated configs use: connected /30 links and /24
host subnets, OSPF (hop-count shortest path, deterministic tie-break),
interface ACLs in/out with first-match-wins and implicit deny.  Every trace
records WHICH ACL line decided the flow, which is what the dependency map and
the counterexample feedback are built from.
"""
from __future__ import annotations

import ipaddress
import heapq
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .iosmodel import Config, parse_config, ParseIssue

PROBES = [("tcp", 22), ("tcp", 80), ("tcp", 443), ("tcp", 8080), ("udp", 53), ("icmp", 0)]


@dataclass(frozen=True)
class Flow:
    src_ip: str
    dst_ip: str
    proto: str
    dport: int


@dataclass
class Trace:
    disposition: str                         # ACCEPTED | DENIED | NO_ROUTE
    path: List[str] = field(default_factory=list)
    deny: Optional[Tuple[str, str, str, str, object]] = None   # device, iface, dir, acl, seq|'implicit'
    deps: Set[Tuple[str, str, str, object]] = field(default_factory=set)


def eval_acl(cfg: Config, acl: Optional[str], flow: Flow):
    """Returns (permitted, seq_or_'implicit', acl_name)."""
    if acl is None:
        return True, None, None
    for line in cfg.acls[acl]:
        if line.matches(flow.proto, flow.src_ip, flow.dst_ip, flow.dport):
            return line.action == "permit", line.seq, acl
    return False, "implicit", acl


def _eval_prefix_list(cfg: Config, name: str, net: ipaddress.IPv4Network):
    """First match wins, implicit deny.  Returns (permitted, seq_or_'implicit')."""
    for line in cfg.prefix_lists[name]:
        if line.matches(net):
            return line.action == "permit", line.seq
    return False, "implicit"


class Network:
    def __init__(self, configs: Dict[str, Config]):
        self.cfg = configs
        self.link_peer: Dict[Tuple[str, str], Tuple[str, str]] = {}    # (dev, iface) -> (peer dev, peer iface)
        self.host_owner: Dict[ipaddress.IPv4Network, Tuple[str, str]] = {}
        self.advertised: Set[ipaddress.IPv4Network] = set()    # host subnets injected into OSPF
        by_net: Dict[ipaddress.IPv4Network, List[Tuple[str, str]]] = {}
        for dev, c in configs.items():
            for ifn, itf in c.interfaces.items():
                if itf.ip is None or itf.shutdown:
                    continue
                covered = self._ospf_covers(c, itf.ip) and itf.vrf is None     # global OSPF only
                if itf.ip.network.prefixlen == 30:
                    if covered:
                        by_net.setdefault(itf.ip.network, []).append((dev, ifn))
                else:
                    self.host_owner[itf.ip.network] = (dev, ifn)   # packets can originate here regardless
                    if covered:
                        self.advertised.add(itf.ip.network)
        for net, ends in by_net.items():
            if len(ends) == 2:
                (a, ia), (b, ib) = ends
                self.link_peer[(a, ia)] = (b, ib)
                self.link_peer[(b, ib)] = (a, ia)
        self.nbrs: Dict[str, List[Tuple[str, str, str]]] = {d: [] for d in configs}   # dev -> (peer, my_if, peer_if)
        for (a, ia), (b, ib) in sorted(self.link_peer.items()):
            self.nbrs[a].append((b, ia, ib))
        # OSPF cost of sending OUT of an interface (IOS default for GigabitEthernet with the default 100 Mb/s
        # reference bandwidth is 1, so with no 'ip ospf cost' lines this is plain hop count)
        self.cost: Dict[Tuple[str, str], int] = {(d, ifn): (itf.ospf_cost or 1)
                                                 for d, c in configs.items() for ifn, itf in c.interfaces.items()}
        self._next: Dict[Tuple[str, str], Optional[Tuple[str, str, str]]] = {}
        self._dist: Dict[str, Dict[str, int]] = {}

    @staticmethod
    def _ospf_covers(c: Config, ifc: ipaddress.IPv4Interface) -> bool:
        return any(ifc.network.subnet_of(n) for n in c.ospf_networks)

    def _dist_to(self, dst_owner: str) -> Dict[str, int]:
        """OSPF distance of every device to dst_owner (Dijkstra on outgoing-interface costs)."""
        if dst_owner in self._dist:
            return self._dist[dst_owner]
        dist = {dst_owner: 0}
        heap = [(0, dst_owner)]
        while heap:
            d, u = heapq.heappop(heap)
            if d > dist.get(u, 10**9):
                continue
            for v, u_if, v_if in self.nbrs.get(u, []):        # v sends to u out of v_if
                nd = d + self.cost[(v, v_if)]
                if nd < dist.get(v, 10**9):
                    dist[v] = nd
                    heapq.heappush(heap, (nd, v))
        self._dist[dst_owner] = dist
        return dist

    def next_hops(self, src: str, dst_owner: str) -> List[Tuple[str, str, str]]:
        """All equal-cost next hops (peer, my_if, peer_if) from src toward dst_owner, sorted."""
        dist = self._dist_to(dst_owner)
        if src not in dist or src == dst_owner:
            return []
        return sorted((v, mi, pi) for v, mi, pi in self.nbrs[src]
                      if v in dist and self.cost[(src, mi)] + dist[v] == dist[src])

    def _route_to(self, src: str, dst_owner: str):
        """Next hop (peer, my_if, peer_if) from src toward dst_owner: shortest path, name tie-break."""
        key = (src, dst_owner)
        if key not in self._next:
            cands = self.next_hops(src, dst_owner)
            self._next[key] = cands[0] if cands else None
        return self._next[key]

    def ecmp_paths(self, flow: "Flow", limit: int = 256) -> List[List[str]]:
        """Every equal-cost device path the flow could take (routing only; ACLs sit on leaf devices)."""
        s, d = self._locate(flow.src_ip), self._locate(flow.dst_ip)
        if s is None or d is None or d[1] not in self.advertised and s[0][0] != d[0][0]:
            return []
        src, dst = s[0][0], d[0][0]
        out: List[List[str]] = []

        def walk(cur, path):
            if len(out) >= limit:
                return
            if cur == dst:
                out.append(path)
                return
            for v, _, _ in self.next_hops(cur, dst):
                if v not in path:
                    walk(v, path + [v])
        walk(src, [src])
        return out

    def _locate(self, ip: str):
        a = ipaddress.ip_address(ip)
        for net, owner in self.host_owner.items():
            if a in net:
                return owner, net
        return None

    def trace(self, flow: Flow) -> Trace:
        s, d = self._locate(flow.src_ip), self._locate(flow.dst_ip)
        if s is None or d is None:
            return Trace("NO_ROUTE")
        src, dst, dst_net = s[0], d[0], d[1]
        t = Trace("NO_ROUTE")
        cur, in_if = src
        src_vrf = self.cfg[cur].interfaces[in_if].vrf
        if src_vrf is not None and not (cur == dst[0] and self.cfg[cur].interfaces[dst[1]].vrf == src_vrf):
            t.path.append(cur)
            t.deps.add((cur, "route", "*", None))
            return t                                          # a VRF with only connected routes: NO_ROUTE
        for _ in range(32):
            t.path.append(cur)
            c = self.cfg[cur]
            t.deps.add((cur, "route", "*", None))
            ok, seq, name = eval_acl(c, c.interfaces[in_if].acl_in, flow)
            if name:
                t.deps.add((cur, "acl", name, seq))
            if not ok:
                t.disposition, t.deny = "DENIED", (cur, in_if, "in", name, seq)
                return t
            if cur == dst[0]:
                out_if = dst[1]
                if c.interfaces[out_if].vrf != c.interfaces[in_if].vrf:
                    t.disposition = "NO_ROUTE"                # different VRF on the same router: no route
                    return t
            else:
                nh = self._route_to(cur, dst[0]) if dst_net in self.advertised else None
                if nh is None:
                    t.disposition = "NO_ROUTE"
                    return t
                if c.ospf_dist_in:                      # inbound OSPF route filter: is the route installed here?
                    permitted, seq = _eval_prefix_list(c, c.ospf_dist_in, dst_net)
                    t.deps.add((cur, "prefixlist", c.ospf_dist_in, seq))
                    if not permitted:
                        t.disposition = "NO_ROUTE"
                        return t
                out_if = nh[1]
            ok, seq, name = eval_acl(c, c.interfaces[out_if].acl_out, flow)
            if name:
                t.deps.add((cur, "acl", name, seq))
            if not ok:
                t.disposition, t.deny = "DENIED", (cur, out_if, "out", name, seq)
                return t
            if cur == dst[0]:
                t.disposition = "ACCEPTED"
                return t
            cur, in_if = nh[0], nh[2]
        return t   # loop guard -> NO_ROUTE


def load_network(texts: Dict[str, str]) -> Network:
    cfgs = {d: parse_config(t, d) for d, t in texts.items()}
    return Network(cfgs)


def host_ip(subnet: str) -> str:
    """Representative host for '10.1.3.0/24' -> 10.1.3.10"""
    return str(ipaddress.ip_network(subnet).network_address + 10)


def all_flows(subs: Optional[List[str]] = None) -> List[Flow]:
    subs = subs or ["10.1.1.0/24", "10.1.2.0/24", "10.1.3.0/24", "10.1.4.0/24"]
    return [Flow(host_ip(a), host_ip(b), p, port) for a in subs for b in subs if a != b for p, port in PROBES]


def diff_flows(a: Network, b: Network, subs: Optional[List[str]] = None) -> List[dict]:
    """Differential reachability: flows whose outcome changed between two snapshots."""
    out = []
    for f in all_flows(subs):
        ta, tb = a.trace(f), b.trace(f)
        if (ta.disposition == "ACCEPTED") != (tb.disposition == "ACCEPTED"):
            out.append(dict(src=f.src_ip, dst=f.dst_ip, proto=f.proto, port=f.dport,
                            before=ta.disposition, after=tb.disposition,
                            blocked_at=list(tb.deny) if tb.deny else None))
    return out
