"""Hard cross-check of Batfish vs the reference engine on the standard beds.

Applies systematic *breaking* edits to each baseline (delete / flip each kind of ACL line, remove an access-group,
shut a host / firewall / core interface, drop an OSPF network statement) and compares the set of broken invariants.
Unlike validate_zoo_batfish.py, most of these edits are expected to break something, so agreement is meaningful.

    python3 scripts/validate_zoo_mutations.py [N_PER_KIND=3] [bed ...]   # needs Batfish running
    python3 scripts/validate_zoo_mutations.py 3 --ref-only               # sanity check without Batfish
Exit code 0 only if every comparison agrees."""
import os, random, re, sys, time
ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
sys.path.insert(0, ROOT)

from cuc.edits import acl_lines, prefix_lines, remove_acl_line, set_acl, set_prefix_list
from cuc.intents import apply_intent, generate_sequence, make_invariant, waypoint_plan
from cuc.invariants import Invariant
from cuc.iosmodel import interface_by_description, set_interface_ospf_cost, set_interface_vrf
from cuc.engine import load_network
from cuc.invariants import ReferenceBackend
from cuc.iosmodel import AclLine, parse_config
from cuc.testbed import STANDARD_BEDS, get_testbed

args = [a for a in sys.argv[1:]]
REF_ONLY = "--ref-only" in args
args = [a for a in args if a != "--ref-only"]
N = int(args[0]) if args and args[0].isdigit() else 3
beds = [a for a in args if a in STANDARD_BEDS] or list(STANDARD_BEDS)


def iface_block(text, pred):
    """Yield (start, end, name, desc) for every interface block whose parsed record satisfies pred."""
    for m in re.finditer(r"^interface (\S+)\n((?: .*\n)*)", text, re.M):
        body = m.group(2)
        desc = re.search(r" description (.*)", body)
        yield m.start(), m.end(), m.group(1), (desc.group(1) if desc else ""), body


def shut(text, ifname):
    for s, e, name, _, body in iface_block(text, None):
        if name == ifname:
            return text[:s] + text[s:e].replace(" no shutdown", " shutdown") + text[e:]
    raise ValueError(ifname)


def mutations(tb, rng, n):
    base, out = tb.baseline, []
    # 1/2: delete / flip ACL lines
    acls = [(dev, name) for dev, name in sorted(set(tb.acl_home.values()))]
    cand = [(dev, name, l) for dev, name in acls for l in acl_lines(base[dev], dev, name)]
    for l in rng.sample(cand, min(n, len(cand))):
        dev, name, line = l
        out.append((f"delete {dev}:{name} line {line.seq}", remove_acl_line(base, dev, name, line.seq)))
    for l in rng.sample(cand, min(n, len(cand))):
        dev, name, line = l
        flipped = [AclLine(x.seq, "deny" if x.action == "permit" else "permit", x.proto, x.src, x.dst, x.dport) if x.seq == line.seq else x
                   for x in acl_lines(base[dev], dev, name)]
        out.append((f"flip {dev}:{name} line {line.seq}", set_acl(base, dev, name, flipped)))
    # 3: remove an access-group (ACL stops applying)
    for dev, name in rng.sample(acls, min(n, len(acls))):
        t = re.sub(rf"^ ip access-group {name} out\n", "", base[dev], flags=re.M)
        out.append((f"unbind {dev}:{name}", {**base, dev: t}))
    # 4: shut an edge host interface, the FW->E link, or a core link interface
    edges = sorted({dev for dev, _ in acl_home_devices(tb)})
    for dev in rng.sample(edges, min(n, len(edges))):
        ifn = next(name for s, e, name, desc, b in iface_block(base[dev], None) if desc == "hosts" or dev == "FW")
        out.append((f"shutdown {dev}:{ifn}", {**base, dev: shut(base[dev], ifn)}))
    cores = [d for d in base if d.startswith("R")]
    for dev in rng.sample(cores, min(n, len(cores))):
        ifs = [name for s, e, name, desc, b in iface_block(base[dev], None)]
        ifn = rng.choice(ifs)
        out.append((f"shutdown {dev}:{ifn}", {**base, dev: shut(base[dev], ifn)}))
    # 5: drop an OSPF network statement
    for dev in rng.sample(sorted(base), min(n, len(base))):
        nets = re.findall(r"^ network .*\n", base[dev], re.M)
        pick = rng.choice(nets)
        out.append((f"drop OSPF {dev}:{pick.strip()}", {**base, dev: base[dev].replace(pick, "", 1)}))
    return out


def acl_home_devices(tb):
    return list(tb.acl_home.values())


def pf_mutations(tb, rng):
    """Route-filter mutations: start from a bed with one gold prefix-filter, then break the filter in each way."""
    key = {c: k for k, c in tb.subnets.items()}
    reach = [i for i in tb.invariants if i.kind == "reach"]
    mkp = lambda src, dst: dict(type="prefix_filter", source=src, destination=dst, protocol="ip", port=0,
                                device=tb.host_dev[key[src]], plist=f"RF_{tb.host_dev[key[src]]}", action="filter")
    cands = []
    for src in sorted(tb.subnets.values()):
        taken = {i.destination for i in reach if i.source == src} | {src}
        # a destination that is reachable now (so the filter has something to do) and has no reach invariant from src
        cands = [(src, c) for c in sorted(tb.subnets.values())
                 if c not in taken and not ref.check(tb.baseline, [make_invariant(1, mkp(src, c))])["N001"].passed]
        if cands:
            break
    src, dst = rng.choice(cands)
    dev = tb.host_dev[key[src]]
    p = mkp(src, dst)
    base = apply_intent(tb.baseline, p)
    inv = make_invariant(1, p)
    lines = prefix_lines(base[dev], dev, p["plist"])
    out = [("[pf] gold filter (control)", base)]
    catch = next(l for l in lines if l.action == "permit")
    out.append((f"[pf] {dev}: drop permit-any (implicit deny)", set_prefix_list(base, dev, p["plist"], [l for l in lines if l is not catch])))
    deny = next(l for l in lines if l.action == "deny")
    out.append((f"[pf] {dev}: drop the deny", set_prefix_list(base, dev, p["plist"], [l for l in lines if l is not deny])))
    out.append((f"[pf] {dev}: unbind distribute-list", {**base, dev: base[dev].replace(f" distribute-list prefix {p['plist']} in\n", "")}))
    flipped = [type(l)(l.seq, "permit" if l is deny else l.action, l.prefix, l.ge, l.le) for l in lines]
    out.append((f"[pf] {dev}: flip deny to permit", set_prefix_list(base, dev, p["plist"], flipped)))
    return out, inv


def wp_vrf_mutations(tb):
    """Start from one gold waypoint step / one gold VRF step on the baseline and break each in several ways."""
    out = []
    its, _ = generate_sequence(5, 1, ref, testbed=tb, intent_types=("waypoint",))
    it = its[0]; p = it["params"]; inv = Invariant(**it["invariant"])
    gold = {**tb.baseline, **it["gold_patch"]}
    out.append(("[wp] gold steering (control)", gold, inv))
    raised, keep = waypoint_plan(tb.baseline, p)
    net = load_network(tb.baseline)
    x, y = raised[0]
    xi, yi = next((a, b) for v, a, b in net.nbrs[x] if v == y)
    undo = {**gold, x: set_interface_ospf_cost(gold[x], xi, None), y: set_interface_ospf_cost(gold[y], yi, None)}
    out.append((f"[wp] undo cost on {x}-{y}", undo, inv))
    a, b = keep[1], keep[2]                                    # a link on the steered path: make it expensive
    ai, bi = next((u, v2) for v, u, v2 in net.nbrs[a] if v == b)
    out.append((f"[wp] raise kept link {a}-{b} to 500", {**gold, a: set_interface_ospf_cost(gold[a], ai, 500), b: set_interface_ospf_cost(gold[b], bi, 500)}, inv))
    out.append((f"[wp] cost on wrong end only", apply_intent(tb.baseline, p, misplaced=True), inv))
    its, _ = generate_sequence(6, 1, ref, testbed=tb, intent_types=("vrf_isolate",))
    it = its[0]; p = it["params"]; inv = Invariant(**it["invariant"]); dev = p["device"]
    gold = {**tb.baseline, **it["gold_patch"]}
    out.append(("[vrf] gold isolation (control)", gold, inv))
    out.append((f"[vrf] {dev}: drop vrf forwarding (leak)", {**gold, dev: gold[dev].replace(" ip vrf forwarding GUEST\n", "")}, inv))
    out.append((f"[vrf] {dev}: hosts iface into VRF instead", apply_intent(tb.baseline, p, misplaced=True), inv))
    up = interface_by_description(gold[dev], lambda d: d.startswith("to "))
    out.append((f"[vrf] {dev}: uplink into VRF too", {**gold, dev: set_interface_vrf(gold[dev], up, "GUEST")}, inv))
    return out


broken = lambda r: sorted(i for i, x in r.items() if not x.passed)
ref = ReferenceBackend()


def all_mutations(tb, n=N):
    """Every mutation for one bed: [(label, candidate texts, invariants to check)]."""
    rng = random.Random(2026)
    muts = [(l, c, tb.invariants) for l, c in mutations(tb, rng, n)]
    pfm, pf_inv = pf_mutations(tb, rng)
    muts += [(l, c, tb.invariants + [pf_inv]) for l, c in pfm]
    muts += [(l, c, tb.invariants + [inv]) for l, c, inv in wp_vrf_mutations(tb)]
    return muts


def main():
    bf = None
    if not REF_ONLY:
        from cuc.batfish_backend import BatfishBackend
        bf = BatfishBackend(network="cuc_zoo_mut")
    total = agree = nonempty = 0
    for name in beds:
        tb = get_testbed(name)
        muts = all_mutations(tb)
        print(f"[{name}] {len(muts)} mutations", flush=True)
        for label, cand, invs in muts:
            t0 = time.time()
            try:
                r = broken(ref.check(cand, invs))
            except Exception as e:
                r = f"PARSE_ISSUE {type(e).__name__}"
            b = None
            if bf is not None:
                try:
                    b = broken(bf.check(cand, invs))
                except Exception as e:
                    b = f"PARSE_ISSUE {type(e).__name__}: {str(e)[:120]}"
            ok = REF_ONLY or r == b or (isinstance(r, str) and isinstance(b, str))
            total += 1; agree += bool(ok); nonempty += bool(r) and not isinstance(r, str)
            print(f"  {'OK  ' if ok else 'DIFF'} {label:50} reference={r}" + ("" if REF_ONLY else f" batfish={b}") + f" ({time.time()-t0:.0f}s)", flush=True)
    print(f"\nMutation validation: {agree}/{total} agree; {nonempty} mutations broke at least one invariant (per reference engine)")
    return 0 if agree == total else 1


if __name__ == "__main__":
    sys.exit(main())
