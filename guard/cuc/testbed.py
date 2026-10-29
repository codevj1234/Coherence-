"""Testbeds: the network under test, its baseline invariants and the ACL placement rules.

A Testbed bundles everything the generator and the harness used to read from module-level globals, so the
same code runs on the original 9-device bed (`legacy_testbed()`) and on larger graphs (`zoo_testbed()`).

Design rule for generated beds (keeps Batfish and the reference engine in agreement):
  * every ACL sits on a LEAF device (an edge router, or the firewall), never on a core router, so the
    outcome of a flow does not depend on which equal-cost path the core picks (ECMP is irrelevant);
  * the firewall is a leaf in front of one host subnet, so "must pass through FW" is true by construction
    for that subnet and the waypoint invariant tests whether FW's ACL / link survive edits.
"""
from __future__ import annotations

import ipaddress
import random
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .invariants import Invariant

W24 = "0.0.0.255"
PORT_POOL = [22, 80, 443, 8080]


@dataclass
class Testbed:
    name: str
    baseline: Dict[str, str]
    invariants: List[Invariant]
    subnets: Dict[int, str]                      # key -> "10.1.k.0/24"
    acl_home: Dict[int, Tuple[str, str]]         # key -> (device, acl name)
    closed_keys: List[int]                       # ACLs with implicit deny at the end
    open_keys: List[int]                         # ACLs ending in 'permit ip any any'
    permit_sources: List[int]
    deny_sources: List[int]
    meta: Dict = field(default_factory=dict)
    host_dev: Dict[int, str] = field(default_factory=dict)          # key -> device that owns the host subnet
    default_intents: Tuple[str, ...] = ("acl",)
    guests: Dict[int, str] = field(default_factory=dict)            # key -> guest subnet on that site's edge router

    @property
    def names(self) -> Dict[str, str]:
        return {c: f"Edge-{k} hosts ({c})" for k, c in self.subnets.items()}

    def subnet_list(self) -> List[str]:
        return [self.subnets[k] for k in sorted(self.subnets)] + [self.guests[k] for k in sorted(self.guests)]

    @property
    def guest_names(self) -> Dict[str, str]:
        return {c: f"Guest network at site {k} ({c})" for k, c in self.guests.items()}


def legacy_testbed() -> Testbed:
    from .invariants import baseline_invariants
    from .topology import ACL_HOME, build_baseline
    sub = {k: f"10.1.{k}.0/24" for k in (1, 2, 3, 4)}
    return Testbed(
        name="legacy9", baseline=build_baseline(), invariants=baseline_invariants(), subnets=sub,
        acl_home={k: ACL_HOME[f"E{k}"] for k in sub}, closed_keys=[1, 2, 4], open_keys=[3],
        permit_sources=[1, 2, 3, 4], deny_sources=[1, 2], meta={"devices": 9, "source": "hand-built"},
        host_dev={k: f"E{k}" for k in sub}, default_intents=("acl",))


# ----------------------------------------------------------------------------------------------
# graph -> testbed
# ----------------------------------------------------------------------------------------------
def load_graph(gml_path: str):
    """Read a Topology Zoo style GML: collapse parallel links, drop self loops, keep the largest component."""
    import networkx as nx
    g = nx.Graph(nx.read_gml(gml_path, label="id"))
    g.remove_edges_from(list(nx.selfloop_edges(g)))
    comp = max(nx.connected_components(g), key=len)
    g = g.subgraph(comp).copy()
    order = sorted(g.nodes)
    mapping = {old: f"R{i + 1}" for i, old in enumerate(order)}
    labels = {mapping[n]: str(g.nodes[n].get("label", n)) for n in order}
    return nx.relabel_nodes(g, mapping), labels


def _spread(g, k: int, rng: random.Random) -> List[str]:
    """k nodes spread over the graph (farthest-point sampling, seeded start)."""
    import networkx as nx
    nodes = sorted(g.nodes)
    chosen = [rng.choice(nodes)]
    dist = {n: nx.single_source_shortest_path_length(g, chosen[0])[n] for n in nodes}
    while len(chosen) < k:
        best = max(sorted(n for n in nodes if n not in chosen), key=lambda n: (dist[n], n))
        chosen.append(best)
        d2 = nx.single_source_shortest_path_length(g, best)
        dist = {n: min(dist[n], d2[n]) for n in nodes}
    return chosen


def _wild(cidr: str) -> str:
    n = ipaddress.ip_network(cidr)
    return f"{n.network_address} {n.hostmask}"


def _render(devices, links, host_subnets, acl_at, fw_guard, guest_subnets=None):
    """devices: list; links: [(a,b)]; host_subnets: {dev: cidr}; acl_at: {dev: {acl: [lines]}};
    fw_guard: (fw_dev, peer_dev, acl_name) -> ACL applied 'out' on fw's interface towards peer_dev."""
    ifaces: Dict[str, List[dict]] = {d: [] for d in devices}
    for n, (a, b) in enumerate(links):
        base = 4 * n
        net = f"10.0.{base // 256}.{base % 256}"
        ia, ib = f"10.0.{base // 256}.{base % 256 + 1}", f"10.0.{base // 256}.{base % 256 + 2}"
        ifaces[a].append(dict(ip=ia, mask="255.255.255.252", desc=f"to {b}", net=net, wild="0.0.0.3", peer=b))
        ifaces[b].append(dict(ip=ib, mask="255.255.255.252", desc=f"to {a}", net=net, wild="0.0.0.3", peer=a))
    for dev, cidr in host_subnets.items():
        n = ipaddress.ip_network(cidr)
        ifaces[dev].append(dict(ip=f"{n.network_address + 1}", mask="255.255.255.0", desc="hosts", net=str(n.network_address),
                                wild=W24, host=True))
    for dev, cidr in (guest_subnets or {}).items():          # guest networks: no ACL, OSPF-advertised
        n = ipaddress.ip_network(cidr)
        ifaces[dev].append(dict(ip=f"{n.network_address + 1}", mask="255.255.255.0", desc="guests", net=str(n.network_address),
                                wild=W24))
    out: Dict[str, str] = {}
    for dev in devices:
        lines = [f"hostname {dev}", "!"]
        for n, itf in enumerate(ifaces[dev]):
            lines += [f"interface GigabitEthernet0/{n}", f" description {itf['desc']}",
                      f" ip address {itf['ip']} {itf['mask']}"]
            if itf.get("host") and dev in acl_at and dev != fw_guard[0]:
                lines.append(f" ip access-group {next(iter(acl_at[dev]))} out")
            if dev == fw_guard[0] and itf.get("peer") == fw_guard[1]:
                lines.append(f" ip access-group {fw_guard[2]} out")
            lines += [" no shutdown", "!"]
        lines.append("router ospf 1")
        for itf in ifaces[dev]:
            lines.append(f" network {itf['net']} {itf['wild']} area 0")
        lines.append("!")
        for acl, body in acl_at.get(dev, {}).items():
            lines.append(f"ip access-list extended {acl}")
            lines += body
            lines.append("!")
        out[dev] = "\n".join(lines) + "\n"
    return out


def zoo_testbed(gml_path: str, n_edges: int, seed: int = 1, target_invariants: int = 20,
                name: Optional[str] = None) -> Testbed:
    """Build a testbed on a Topology Zoo graph.

    Every graph node becomes a core router (R1..Rn, OSPF area 0, /30 links).  n_edges edge routers E1..En hang
    off spread-out core nodes, each owning a /24 and an ACL on its host interface.  One more subnet (key n+1)
    sits behind a firewall FW that hangs off another core node; FW holds that subnet's ACL."""
    import os
    rng = random.Random(seed)
    g, labels = load_graph(gml_path)
    k = n_edges
    if g.number_of_nodes() < k + 1:
        raise ValueError(f"graph has only {g.number_of_nodes()} nodes, need {k + 1} attachment points")
    attach = _spread(g, k + 1, rng)
    fw_key = k + 1
    keys = list(range(1, k + 2))
    subnets = {i: f"10.1.{i}.0/24" for i in keys}

    core = sorted(g.nodes, key=lambda x: int(x[1:]))
    edge_dev = {i: f"E{i}" for i in keys}               # E{k+1} is the host device behind FW
    devices = core + [edge_dev[i] for i in keys] + ["FW"]
    links = [tuple(sorted(e, key=lambda x: int(x[1:]))) for e in sorted(g.edges, key=lambda e: tuple(sorted(int(x[1:]) for x in e)))]
    for i in range(1, k + 1):
        links.append((attach[i - 1], edge_dev[i]))
    links.append((attach[k], "FW"))
    links.append(("FW", edge_dev[fw_key]))

    n_open = max(2, k // 2)
    open_keys = sorted(rng.sample(list(range(1, k + 1)), n_open))
    closed_keys = [i for i in keys if i not in open_keys]
    acl_name = {i: f"TO_E{i}" for i in keys}
    lines: Dict[int, List[Tuple[int, str]]] = {i: [] for i in keys}      # (seq, text)
    invs: List[Tuple[str, str, str, dict, Optional[str]]] = []          # kind, src, dst, headers, via
    used = set()

    def line(i, action, proto, s, tail=""):
        seq = 10 * (len(lines[i]) + 1)
        lines[i].append((seq, f" {seq} {action} {proto} {_wild(subnets[s])} {_wild(subnets[i])}{tail}"))

    def add_permit(d):
        for _ in range(50):
            s, p = rng.choice([x for x in keys if x != d]), rng.choice(PORT_POOL)
            if (s, d, p) not in used:
                used.add((s, d, p))
                line(d, "permit", "tcp", s, f" eq {p}")
                invs.append(("reach", subnets[s], subnets[d], {"protocol": "tcp", "ports": [p]}, None))
                return True
        return False

    def add_deny(d, whole_ip=False):
        for _ in range(50):
            s, p = rng.choice([x for x in keys if x != d]), rng.choice(PORT_POOL)
            if (s, d, p) not in used and not (whole_ip and (s, d, "ip") in used):
                used.add((s, d, "ip" if whole_ip else p))
                if whole_ip:
                    line(d, "deny", "ip", s)
                    invs.append(("isolate", subnets[s], subnets[d], {"protocol": "ip"}, None))
                else:
                    line(d, "deny", "tcp", s, f" eq {p}")
                    invs.append(("isolate", subnets[s], subnets[d], {"protocol": "tcp", "ports": [p]}, None))
                return True
        return False

    def add_implicit_isolate(d):
        permitted = {(s, p) for (s, dd, p) in used if dd == d and isinstance(p, int)}
        for _ in range(50):
            s, p = rng.choice([x for x in keys if x != d]), rng.choice(PORT_POOL + [3306, 8443])
            if (s, p) not in permitted and (s, d, ("iso", p)) not in used:
                used.add((s, d, ("iso", p)))
                invs.append(("isolate", subnets[s], subnets[d], {"protocol": "tcp", "ports": [p]}, None))
                return True
        return False

    # mandatory structure: one permit per closed ACL, one deny per open ACL, one waypoint
    for d in closed_keys:
        add_permit(d)
    for d in open_keys:
        add_deny(d)
    # the waypoint invariant mirrors the first permit into the firewalled subnet
    fw_perm = next(inv for inv in invs if inv[2] == subnets[fw_key])
    n_way = 1
    # fill up to the target with a seeded mix of extras
    while len(invs) + n_way < target_invariants:
        r = rng.random()
        if r < 0.40:
            add_permit(rng.choice(closed_keys))
        elif r < 0.65:
            add_deny(rng.choice(open_keys), whole_ip=rng.random() < 0.2)
        else:
            add_implicit_isolate(rng.choice(closed_keys))
    invs.append(("waypoint", fw_perm[1], fw_perm[2], fw_perm[3], "FW"))

    # ACL bodies
    acl_at: Dict[str, Dict[str, List[str]]] = {}
    for i in keys:
        body = [t for _, t in lines[i]]
        if i in open_keys:
            body.append(f" {10 * (len(body) + 1)} permit ip any any")
        dev = "FW" if i == fw_key else edge_dev[i]
        acl_at.setdefault(dev, {})[acl_name[i]] = body
    host_subnets = {edge_dev[i]: subnets[i] for i in keys}
    guests = {i: f"10.2.{i}.0/24" for i in range(1, k + 1)}
    configs = _render(devices, links, host_subnets, acl_at, ("FW", edge_dev[fw_key], acl_name[fw_key]),
                      {edge_dev[i]: guests[i] for i in guests})

    # invariant ids R01.. I01.. W01
    counters = {"reach": 0, "isolate": 0, "waypoint": 0}
    pre = {"reach": "R", "isolate": "I", "waypoint": "W"}
    inv_objs = []
    for kind, s, d, hdr, via in invs:
        counters[kind] += 1
        inv_objs.append(Invariant(f"{pre[kind]}{counters[kind]:02d}", kind, s, d, hdr, via=via))
    tb = Testbed(
        name=name or os.path.splitext(os.path.basename(gml_path))[0].lower(),
        baseline=configs, invariants=inv_objs, subnets=subnets,
        acl_home={i: (("FW" if i == fw_key else edge_dev[i]), acl_name[i]) for i in keys},
        closed_keys=closed_keys, open_keys=open_keys, permit_sources=keys, deny_sources=keys,
        host_dev=dict(edge_dev), default_intents=("acl", "prefix_filter", "waypoint", "vrf_isolate"), guests=guests,
        meta=dict(graph=os.path.basename(gml_path), core_nodes=g.number_of_nodes(), core_links=g.number_of_edges(),
                  devices=len(devices), edges=k, fw_attach=attach[k], edge_attach={f"E{i}": attach[i - 1] for i in range(1, k + 1)},
                  invariants=len(inv_objs), open_keys=open_keys, seed=seed, labels=labels))
    return tb


# ----------------------------------------------------------------------------------------------
# registry of the standard beds (graph file under data/, number of edge subnets, config seed)
# ----------------------------------------------------------------------------------------------
STANDARD_BEDS = {
    "abilene": ("Abilene.gml", 4, 1),          # 11 core nodes -> 17 devices
    "bteurope": ("BtEurope.gml", 6, 1),        # 24 core nodes -> 32 devices
    "cesnet": ("Cesnet201006.gml", 8, 1),      # 52 core nodes -> 62 devices
}


def get_testbed(name: str, data_dir: Optional[str] = None) -> Testbed:
    import os
    if name in ("legacy", "legacy9"):
        return legacy_testbed()
    fn, k, seed = STANDARD_BEDS[name]
    root = data_dir or os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
    return zoo_testbed(os.path.join(root, fn), k, seed=seed, name=name)
