"""Intent templates, gold patches, trap labelling and the seeded sequence generator (Phase 2)."""
from __future__ import annotations

import ipaddress
import json
import random
from typing import Dict, List, Optional, Tuple

from .edits import Texts, acl_lines, resequence, set_acl
from .invariants import Invariant
from .edits import prefix_lines, set_prefix_list
from .engine import Flow, host_ip, load_network
from .iosmodel import (AclLine, PrefixLine, ensure_vrf, interface_by_description, parse_acl_line, parse_config,
                       set_interface_ospf_cost, set_interface_vrf)
from .testbed import Testbed, legacy_testbed

SUBNET = {1: "10.1.1.0/24", 2: "10.1.2.0/24", 3: "10.1.3.0/24", 4: "10.1.4.0/24"}
NAME = {SUBNET[k]: f"Edge-{k} hosts ({SUBNET[k]})" for k in SUBNET}
PORTS = [21, 22, 25, 53, 80, 110, 143, 443, 445, 993, 1433, 3306, 3389, 5432, 8080, 8443]

PERMIT_TEXT = [
    "Allow {S} to reach {D} on {P} port {N}.",
    "Permit {P}/{N} traffic from {S} to {D}.",
    "Open {P} port {N} so that {S} can connect to {D}.",
    "Add a rule letting {S} access {D} over {P} {N}.",
    "Hosts in {S} need {P} {N} access to {D}; allow it.",
]
DENY_TEXT = [
    "Block {S} from reaching {D} on {P} port {N}.",
    "Deny {P}/{N} traffic from {S} to {D}.",
    "Close {P} port {N} between {S} and {D}.",
    "Add a rule stopping {S} from using {P} {N} to {D}.",
    "{S} must no longer connect to {D} over {P} {N}; block it.",
]


PF_TEXT = [
    "Router {R} must no longer learn the route to {D}.",
    "Filter {D} out of the OSPF routes that {R} accepts.",
    "Stop {R} from installing a route to {D}.",
    "On {R}, block the OSPF route for {D} with a prefix-list.",
    "{R} should have no route to {D}; filter it inbound in OSPF.",
]
CATCH_ALL = ipaddress.ip_network("0.0.0.0/0")

WP_TEXT = [
    "Traffic from {S} to {D} must pass through router {W}.",
    "Steer {S} to {D} traffic via {W} by adjusting OSPF link costs.",
    "Make sure every path from {S} to {D} traverses {W}.",
    "Route {S}-to-{D} flows through {W}; change OSPF costs as needed.",
    "{S} must reach {D} only by way of {W}.",
]
WP_STEP = 10            # cost added to a link per steering step

VRF_TEXT = [
    "Move the {S} into an isolated VRF so it can no longer reach {D}.",
    "Quarantine the {S} in its own VRF on {R}; it must not reach {D}.",
    "Put the {S} interface on {R} into VRF {V} to cut it off from {D}.",
    "Isolate the {S} from {D} by placing it in a separate VRF on {R}.",
    "The {S} must lose access to {D}: segment it into VRF {V} on {R}.",
]


def _apply_vrf(texts: Texts, p: dict, broad: bool, misplaced: bool, syntax_error: bool) -> Texts:
    """Gold: define the VRF and put the guest interface into it (no OSPF there, so the guest network can reach
    nothing outside its own subnet).  misplaced = the hosts interface is moved instead (the guests stay connected:
    an isolation leak, and the site's hosts are cut off); broad = the uplink is moved too (the whole site goes dark)."""
    dev, vrf = p["device"], p["vrf"]
    t = ensure_vrf(texts[dev], vrf)
    target = interface_by_description(t, (lambda d: d == "hosts") if misplaced else (lambda d: d == "guests"))
    t = set_interface_vrf(t, target, vrf)
    if broad:
        t = set_interface_vrf(t, interface_by_description(t, lambda d: d.startswith("to ")), vrf)
    if syntax_error:
        t = t.replace(f" ip vrf forwarding {vrf}", f" ip vrff forwarding {vrf}", 1)
    return {**texts, dev: t}


def _wp_flow(p) -> Flow:
    return Flow(host_ip(p["source"]), host_ip(p["destination"]), p["protocol"], p["port"])


def _path_toward(net, a: str, b: str) -> List[str]:
    path = [a]
    while path[-1] != b:
        nh = net.next_hops(path[-1], b)
        if not nh or nh[0][0] in path:
            raise ValueError("no loop-free path")
        path.append(nh[0][0])
    return path


def _ends(net, x: str, y: str) -> Tuple[str, str]:
    """(x's interface toward y, y's interface toward x)."""
    return next((xi, yi) for v, xi, yi in net.nbrs[x] if v == y)


def waypoint_plan(texts: Texts, p: dict, max_steps: int = 40):
    """Greedy OSPF steering: keep the cheapest path through the waypoint, and raise (both ends of) one link on
    a path that avoids it until every equal-cost path crosses the waypoint.  Returns ([(x, y)], keep_path)."""
    net = load_network(texts)
    f = _wp_flow(p)
    src = net._locate(f.src_ip)[0][0]
    dst = net._locate(f.dst_ip)[0][0]
    w = p["via"]
    keep = _path_toward(net, src, w)[:-1] + _path_toward(net, w, dst)
    if len(set(keep)) != len(keep):
        raise ValueError("path through the waypoint revisits a device")
    keep_edges = {frozenset(e) for e in zip(keep, keep[1:])}
    raised: List[Tuple[str, str]] = []
    cur = texts
    for _ in range(max_steps):
        paths = net.ecmp_paths(f)
        bad = [pa for pa in paths if w not in pa]
        if not bad:
            return raised, keep
        edge = next(((x, y) for pa in bad for x, y in zip(pa, pa[1:]) if frozenset((x, y)) not in keep_edges), None)
        if edge is None:
            raise ValueError("cannot steer: every avoiding path shares the kept path")
        x, y = edge
        raised.append(edge)
        cur = _raise_link(cur, net, x, y, both=True)
        net = load_network(cur)
    raise ValueError("steering did not converge")


def _raise_link(texts: Texts, net, x: str, y: str, both: bool = True, only_far_end: bool = False) -> Texts:
    xi, yi = _ends(net, x, y)
    out = dict(texts)
    if not only_far_end:
        out[x] = set_interface_ospf_cost(out[x], xi, net.cost[(x, xi)] + WP_STEP)
    if both or only_far_end:
        out[y] = set_interface_ospf_cost(out[y], yi, net.cost[(y, yi)] + WP_STEP)
    return out


def _apply_waypoint(texts: Texts, p: dict, broad: bool, misplaced: bool, syntax_error: bool) -> Texts:
    """Gold: raise both ends of each link in the steering plan.  misplaced = raise only the far end of each link
    (the cost is applied in the wrong direction, so the path does not move); broad = also raise every other link
    that touches an avoiding path (over-steering: drags unrelated flows around)."""
    raised, keep = waypoint_plan(texts, p)
    out, net = dict(texts), load_network(texts)
    if broad:
        f = _wp_flow(p)
        keep_edges = {frozenset(e) for e in zip(keep, keep[1:])}
        extra = []
        for pa in net.ecmp_paths(f):
            for x in pa:
                for v, _, _ in net.nbrs[x]:
                    e = frozenset((x, v))
                    if e not in keep_edges and x.startswith("R") and v.startswith("R") and (x, v) not in extra and (v, x) not in extra:
                        extra.append((x, v))
        raised = raised + [e for e in extra if e not in raised]
    for x, y in raised:
        out = _raise_link(out, load_network(out), x, y, both=not misplaced, only_far_end=misplaced)
    if syntax_error:
        x, y = raised[0]
        out[x] = out[x].replace(" ip ospf cost ", " ip ospf costt ", 1)
    return out


def _is_catch_all(l: PrefixLine) -> bool:
    return l.action == "permit" and l.prefix == CATCH_ALL and l.le == 32


def _apply_prefix_filter(texts: Texts, p: dict, broad: bool, misplaced: bool, syntax_error: bool) -> Texts:
    """Gold: deny the destination prefix in an inbound OSPF distribute-list on the source's host router,
    ending the prefix-list with 'permit 0.0.0.0/0 le 32' (prefix-lists end in an implicit deny).
    broad = deny 10.1.0.0/16 le 32 (every host subnet), misplaced = new list without the trailing permit
    (implicit deny drops every route), or - if the list already exists - a deny placed after the permit (shadowed)."""
    dev, name = p["device"], p["plist"]
    lines = prefix_lines(texts[dev], dev, name)
    existed = bool(lines)
    deny = PrefixLine(0, "deny", ipaddress.ip_network("10.1.0.0/16") if broad else ipaddress.ip_network(p["destination"]),
                      None, 32 if broad else None)
    catch = next((l for l in lines if _is_catch_all(l)), None)
    if not existed:
        new = PrefixLine(10, "deny", deny.prefix, deny.ge, deny.le)
        lines = [new] + ([] if misplaced else [PrefixLine(1000, "permit", CATCH_ALL, None, 32)])
    else:
        if misplaced and catch is not None:
            seq = catch.seq + 10                                       # after the catch-all: never reached
        else:
            seq = max([l.seq for l in lines if l is not catch], default=0) + 10
        new = PrefixLine(seq, "deny", deny.prefix, deny.ge, deny.le)
        lines = lines + [new]
    out = set_prefix_list(texts, dev, name, lines, bind=True)
    if syntax_error:
        good = new.render(name)
        out[dev] = out[dev].replace(good, good.replace(" deny ", " denyy ", 1), 1)
    return out


def _wild(net: str) -> str:
    n = ipaddress.ip_network(net)
    return f"{n.network_address} {n.hostmask}"


def apply_intent(texts: Texts, p: dict, broad: bool = False, misplaced: bool = False,
                 syntax_error: bool = False) -> Texts:
    """Apply an intent to a set of device configs.
    Default = the correct (gold) edit.  broad = drop the port/protocol qualifier (over-broad rule),
    misplaced = put the rule at the wrong end of the ACL, syntax_error = emit an unparsable line."""
    if p.get("type") == "prefix_filter":
        return _apply_prefix_filter(texts, p, broad, misplaced, syntax_error)
    if p.get("type") == "waypoint":
        return _apply_waypoint(texts, p, broad, misplaced, syntax_error)
    if p.get("type") == "vrf_isolate":
        return _apply_vrf(texts, p, broad, misplaced, syntax_error)
    dev, acl, action = p["device"], p["acl"], p["action"]
    proto = "ip" if broad else p["protocol"]
    tail = "" if broad else f" eq {p['port']}"
    lines = resequence(acl_lines(texts[dev], dev, acl))
    probe = parse_acl_line(f" 1 {action} {proto} {_wild(p['source'])} {_wild(p['destination'])}{tail}")

    def mk(seq):
        return AclLine(seq, probe.action, probe.proto, probe.src, probe.dst, probe.dport)

    catch_all = [i for i, l in enumerate(lines) if l.action == "permit" and l.proto == "ip"
                 and l.src.prefixlen == 0 and l.dst.prefixlen == 0]
    if action == "permit":
        new = mk(5) if misplaced else mk((lines[-1].seq + 10) if lines else 10)
        lines = lines + [new]
    else:
        if misplaced or not catch_all:
            new = mk((lines[-1].seq + 10) if lines else 10)       # after the catch-all: shadowed
            lines = lines + [new]
        else:
            idx = catch_all[-1]
            new = mk(lines[idx].seq - 5)
            lines = lines + [new]
    out = set_acl(texts, dev, acl, lines)
    if syntax_error:
        good = new.render()
        out[dev] = out[dev].replace(good, good.replace(action, action + "t", 1), 1)
    return out


def make_invariant(step: int, p: dict) -> Invariant:
    if p.get("type") == "waypoint":
        return Invariant(f"N{step:03d}", "waypoint", p["source"], p["destination"],
                         {"protocol": p["protocol"], "ports": [p["port"]]}, via=p["via"], created_at_step=step)
    if p.get("type") in ("prefix_filter", "vrf_isolate"):          # no route at all -> every probe must fail
        return Invariant(f"N{step:03d}", "isolate", p["source"], p["destination"], {"protocol": "ip"}, created_at_step=step)
    kind = "reach" if p["action"] == "permit" else "isolate"
    return Invariant(f"N{step:03d}", kind, p["source"], p["destination"],
                     {"protocol": p["protocol"], "ports": [p["port"]]}, created_at_step=step)


def _violations(backend, texts, tracked: List[Invariant], new_inv: Invariant, holding_ids) -> Tuple[List[str], bool]:
    res = backend.check(texts, tracked + [new_inv])
    broken = [i for i in holding_ids if not res[i].passed]
    return broken, res[new_inv.id].passed


def label_trap(backend, gold_state: Texts, p: dict, tracked, new_inv, holding_ids) -> Optional[str]:
    """A step is a trap if a plausible naive edit breaks an earlier invariant or misses the intent."""
    def bad(**kw):
        try:
            broken, ok = _violations(backend, apply_intent(gold_state, p, **kw), tracked, new_inv, holding_ids)
        except Exception:
            return True
        return bool(broken) or not ok
    broad, shadow = bad(broad=True), bad(misplaced=True)
    shadow_name, broad_name = "shadowing", "prefix_overlap"
    if p.get("type") == "prefix_filter" and not prefix_lines(gold_state[p["device"]], p["device"], p["plist"]):
        shadow_name = "implicit_deny"             # new prefix-list without the trailing permit
    if p.get("type") == "waypoint":
        shadow_name, broad_name = "cost_wrong_end", "over_steering"
    if p.get("type") == "vrf_isolate":
        shadow_name, broad_name = "isolation_leak", "over_isolation"
    if broad and shadow:
        return f"{shadow_name}+{broad_name}"
    return broad_name if broad else (shadow_name if shadow else None)


def generate_sequence(seed: int, length: int, backend, trap_target: float = 0.30,
                      seq_id: Optional[str] = None, testbed: Optional[Testbed] = None,
                      intent_types: Optional[Tuple[str, ...]] = None, pf_share: float = 0.35,
                      mix: Optional[Dict[str, float]] = None, require_nontrivial: Optional[bool] = None) -> Tuple[List[dict], dict]:
    """require_nontrivial (default: on for any explicit testbed): an intent whose new invariant already holds
    before the edit is discarded - otherwise a model could 'succeed' by changing nothing.  The frozen legacy
    sequences were generated without this check (they contain no such steps: verified for seeds 100 and 107)."""
    explicit = testbed is not None and testbed.name != "legacy9"     # new-bed behaviour; legacy stays frozen
    nontrivial = explicit if require_nontrivial is None else require_nontrivial
    tb = testbed or legacy_testbed()
    types = tuple(intent_types or tb.default_intents)
    SUBNET_, NAME_ = tb.subnets, {**tb.names, **tb.guest_names}
    rng = random.Random(seed)
    seq_id = seq_id or f"seq{seed:03d}"
    base_invs = list(tb.invariants)
    gold = dict(tb.baseline)
    tracked: List[Invariant] = list(base_invs)
    holding = {i.id for i in tracked}
    used = {(i.source, i.destination, i.headers["protocol"], i.headers["ports"][0])
            for i in base_invs if i.headers.get("ports")}
    out, discarded, traps = [], 0, 0

    # share of candidate draws per non-ACL type (ACL takes the rest); a legacy ("acl",) run draws no type at all
    weights = dict(prefix_filter=pf_share, waypoint=0.30, vrf_isolate=0.20)
    weights.update(mix or {})

    def pick_type():
        if types == ("acl",):
            return "acl"
        if len(types) == 1:
            return types[0]
        r, acc = rng.random(), 0.0
        for t in ("prefix_filter", "waypoint", "vrf_isolate"):
            if t in types:
                acc += weights[t]
                if r < acc:
                    return t
        return "acl" if "acl" in types else types[0]

    def waypoint_candidate():
        reach = [i for i in tracked if i.kind == "reach" and i.headers.get("ports") and i.id in holding]
        if not reach:
            return None
        r = rng.choice(reach)
        p0 = dict(source=r.source, destination=r.destination, protocol=r.headers["protocol"], port=r.headers["ports"][0])
        net = load_network(gold)
        f = _wp_flow(p0)
        paths = net.ecmp_paths(f)
        if not paths:
            return None
        a, b = paths[0][0], paths[0][-1]
        on_all = set.intersection(*[set(pa) for pa in paths])
        da, db = net._dist_to(b), None
        cands = []
        for w in sorted(d for d in net.nbrs if d.startswith("R") and d not in on_all):
            dw = net._dist_to(w)
            if a in dw and w in da and dw[a] + da[w] <= da[a] + 2:
                cands.append(w)
        if not cands:
            return None
        w = rng.choice(cands)
        region = sorted(set().union(*[set(pa) for pa in paths]) | {w})
        p = dict(type="waypoint", via=w, device=a, devices=region, action="steer", **p0)
        try:
            raised, keep = waypoint_plan(gold, p)
        except ValueError:
            return None
        p["devices"] = sorted(set(region) | set(keep) | {d for e in raised for d in e})
        if len(p["devices"]) > 14:
            return None
        return p

    def vrf_candidate():
        if not tb.guests:
            return None
        i = rng.choice(sorted(tb.guests))
        targets = [c for c in tb.subnet_list() if c != tb.guests[i]]
        dev = tb.host_dev[i]
        return dict(type="vrf_isolate", source=tb.guests[i], destination=rng.choice(targets), protocol="ip", port=0,
                    device=dev, vrf="GUEST", action="isolate")

    # Explicit testbeds: each step first draws its intent type from a target mix (so every type gets enough
    # samples for per-type results), then draws candidates of that type; if none is valid it falls back to any.
    STEP_MIX = dict(acl_permit=0.40, acl_deny=0.15, prefix_filter=0.15, waypoint=0.15, vrf_isolate=0.15)
    if mix:
        STEP_MIX.update({k: v for k, v in mix.items() if k in STEP_MIX})
    enabled = [t for t in STEP_MIX if (t if not t.startswith("acl_") else "acl") in types]

    def step_type():
        tot = sum(STEP_MIX[t] for t in enabled)
        r, acc = rng.random() * tot, 0.0
        for t in enabled:
            acc += STEP_MIX[t]
            if r < acc:
                return t
        return enabled[-1]

    def acl_candidate(action):
        if action == "permit":
            d = rng.choice(tb.closed_keys); s = rng.choice([x for x in tb.permit_sources if x != d])
        else:
            d = rng.choice(tb.open_keys); s = rng.choice([x for x in tb.deny_sources if x != d])
        dev, acl = tb.acl_home[d]
        return dict(source=SUBNET_[s], destination=SUBNET_[d], protocol="tcp", port=rng.choice(PORTS),
                    device=dev, acl=acl, action=action)

    def typed_candidate(t):
        if t == "waypoint":
            return waypoint_candidate()
        if t == "vrf_isolate":
            return vrf_candidate()
        if t == "prefix_filter":
            s = rng.choice(sorted(SUBNET_)); d = rng.choice([x for x in sorted(SUBNET_) if x != s])
            dev = tb.host_dev[s]
            return dict(type="prefix_filter", source=SUBNET_[s], destination=SUBNET_[d], protocol="ip", port=0,
                        device=dev, plist=f"RF_{dev}", action="filter")
        return acl_candidate("permit" if t == "acl_permit" else "deny")

    def candidate():
        t = pick_type()
        if t == "waypoint":
            return waypoint_candidate()
        if t == "vrf_isolate":
            return vrf_candidate()
        if t == "prefix_filter":
            s = rng.choice(sorted(SUBNET_)); d = rng.choice([x for x in sorted(SUBNET_) if x != s])
            dev = tb.host_dev[s]
            return dict(type="prefix_filter", source=SUBNET_[s], destination=SUBNET_[d], protocol="ip", port=0,
                        device=dev, plist=f"RF_{dev}", action="filter")
        if rng.random() < 0.6:        # permit into a closed ACL
            d = rng.choice(tb.closed_keys); s = rng.choice([x for x in tb.permit_sources if x != d]); action = "permit"
        else:                         # deny into an open ACL
            d = tb.open_keys[0] if len(tb.open_keys) == 1 else rng.choice(tb.open_keys)
            s = rng.choice([x for x in tb.deny_sources if x != d]); action = "deny"
        dev, acl = tb.acl_home[d]
        return dict(source=SUBNET_[s], destination=SUBNET_[d], protocol="tcp", port=rng.choice(PORTS),
                    device=dev, acl=acl, action=action)

    step = 0
    while step < length:
        want_trap = rng.random() < trap_target
        chosen = None
        this_type = step_type() if explicit else None
        first_valid = None
        for attempt in range(400 if explicit else 60):
            typed = this_type is not None and attempt < 150
            if this_type is not None and not typed and chosen is not None:
                break                     # keep the step's drawn type even if its trap status is not the wanted one
            if typed and first_valid is not None and attempt - first_valid >= 20:
                break                     # a valid candidate of this type exists; stop searching for a trap match
            p = typed_candidate(this_type) if typed else candidate()
            if p is None:
                discarded += 1
                continue
            key = (p["source"], p["destination"], p["protocol"], p["port"]) if p.get("type") != "waypoint" \
                else (p["source"], p["destination"], f"waypoint:{p['via']}", p["port"])
            if key in used:
                continue
            new_inv = make_invariant(step + 1, p)
            if nontrivial or p.get("type") in ("prefix_filter", "vrf_isolate"):
                # only meaningful if the new policy does NOT already hold before the edit
                try:
                    already = backend.check(gold, [new_inv])[new_inv.id].passed
                except Exception:
                    already = True
                if already:
                    discarded += 1
                    used.add(key)
                    continue
            try:
                new_state = apply_intent(gold, p)
                broken, ok = _violations(backend, new_state, tracked, new_inv, holding)
            except Exception:
                discarded += 1
                continue
            if broken or not ok:          # gold patch must be clean and achieve the intent
                discarded += 1
                used.add(key)
                continue
            kind = label_trap(backend, gold, p, tracked, new_inv, holding)
            chosen = (p, new_inv, new_state, kind, key)
            if first_valid is None:
                first_valid = attempt
            if (kind is not None) == want_trap:
                break
        if chosen is None:
            raise RuntimeError("could not find a valid intent")
        p, new_inv, new_state, kind, key = chosen
        step += 1
        used.add(key)
        if p.get("type") == "prefix_filter":
            text = rng.choice(PF_TEXT).format(R=p["device"], D=NAME_[p["destination"]])
        elif p.get("type") == "vrf_isolate":
            text = rng.choice(VRF_TEXT).format(S=NAME_[p["source"]].split(" (")[0].lower(),
                                               D=NAME_[p["destination"]], R=p["device"], V=p["vrf"])
        elif p.get("type") == "waypoint":
            text = rng.choice(WP_TEXT).format(S=NAME_[p["source"]], D=NAME_[p["destination"]], W=p["via"]) + \
                f" (applies to {p['protocol'].upper()} port {p['port']})"
        else:
            text = rng.choice(PERMIT_TEXT if p["action"] == "permit" else DENY_TEXT).format(
                S=NAME_[p["source"]], D=NAME_[p["destination"]], P=p["protocol"].upper(), N=p["port"])
        out.append(dict(
            id=f"{seq_id}-{step:03d}", sequence_id=seq_id, step=step,
            type=p["type"] if p.get("type") in ("prefix_filter", "waypoint", "vrf_isolate")
            else ("acl_permit" if p["action"] == "permit" else "acl_deny"),
            params=p, text=text, adds_invariant=new_inv.id, invariant=new_inv.__dict__,
            trap=kind is not None, trap_kind=kind,
            gold_patch={d: new_state[d] for d in sorted(new_state) if new_state[d] != gold[d]} or {p["device"]: new_state[p["device"]]},
        ))
        traps += kind is not None
        gold = new_state
        tracked.append(new_inv)
        holding.add(new_inv.id)
    meta = dict(seq_id=seq_id, seed=seed, length=length, discarded_candidates=discarded,
                trap_steps=traps, trap_ratio=round(traps / length, 3))
    if testbed is not None:
        meta["testbed"] = tb.name
        meta["intent_types"] = list(types)
    return out, meta


def write_sequence(path: str, intents: List[dict], meta: dict) -> None:
    with open(path, "w") as f:
        f.write(json.dumps({"meta": meta}, sort_keys=True) + "\n")
        for it in intents:
            f.write(json.dumps(it, sort_keys=True) + "\n")


def read_sequence(path: str) -> Tuple[List[dict], dict]:
    with open(path) as f:
        rows = [json.loads(l) for l in f]
    return rows[1:], rows[0]["meta"]
