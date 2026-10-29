"""Parser and renderer for the Cisco-IOS-style subset used in this project.

Strict on purpose: anything we do not understand raises ParseIssue, which the
harness records as PARSE_FAIL (the analogue of Batfish's initIssues /
fileParseStatus warnings).
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

ANY = ipaddress.ip_network("0.0.0.0/0")
PROTOS = ("ip", "tcp", "udp", "icmp")


class ParseIssue(Exception):
    pass


@dataclass(frozen=True)
class AclLine:
    seq: int
    action: str                      # permit | deny
    proto: str                       # ip | tcp | udp | icmp
    src: ipaddress.IPv4Network
    dst: ipaddress.IPv4Network
    dport: Optional[Tuple[int, int]]  # inclusive range, None = any port

    def matches(self, proto: str, src_ip, dst_ip, dport: int) -> bool:
        if self.proto != "ip" and self.proto != proto:
            return False
        if ipaddress.ip_address(src_ip) not in self.src:
            return False
        if ipaddress.ip_address(dst_ip) not in self.dst:
            return False
        if self.dport is not None:
            if proto not in ("tcp", "udp"):
                return False
            lo, hi = self.dport
            if not (lo <= dport <= hi):
                return False
        return True

    def render(self) -> str:
        return f" {self.seq} {self.action} {self.proto} {_fmt_net(self.src)} {_fmt_net(self.dst)}" + _fmt_port(self.dport)


@dataclass(frozen=True)
class PrefixLine:
    seq: int
    action: str                      # permit | deny
    prefix: ipaddress.IPv4Network
    ge: Optional[int] = None
    le: Optional[int] = None

    def matches(self, net: ipaddress.IPv4Network) -> bool:
        if not net.subnet_of(self.prefix):
            return False
        if self.ge is None and self.le is None:
            return net.prefixlen == self.prefix.prefixlen
        lo = self.ge if self.ge is not None else self.prefix.prefixlen
        hi = self.le if self.le is not None else 32
        return lo <= net.prefixlen <= hi

    def render(self, name: str) -> str:
        t = f"ip prefix-list {name} seq {self.seq} {self.action} {self.prefix}"
        if self.ge is not None:
            t += f" ge {self.ge}"
        if self.le is not None:
            t += f" le {self.le}"
        return t


def parse_prefix_line(tokens: List[str]) -> Tuple[str, PrefixLine]:
    """tokens after 'ip prefix-list': NAME seq N permit|deny A.B.C.D/L [ge X] [le Y]"""
    if len(tokens) < 5 or tokens[1] != "seq" or not tokens[2].isdigit():
        raise ParseIssue(f"unrecognized prefix-list line: 'ip prefix-list {' '.join(tokens)}'")
    name, seq, action = tokens[0], int(tokens[2]), tokens[3]
    if action not in ("permit", "deny"):
        raise ParseIssue(f"unknown prefix-list action '{action}'")
    try:
        prefix = ipaddress.ip_network(tokens[4], strict=True)
    except ValueError as e:
        raise ParseIssue(f"bad prefix '{tokens[4]}': {e}")
    if prefix.version != 4:
        raise ParseIssue("only IPv4 prefixes are supported")
    ge = le = None
    rest = tokens[5:]
    while rest:
        if len(rest) < 2 or rest[0] not in ("ge", "le") or not rest[1].isdigit():
            raise ParseIssue(f"unrecognized prefix-list tail: '{' '.join(rest)}'")
        if rest[0] == "ge":
            ge = int(rest[1])
        else:
            le = int(rest[1])
        rest = rest[2:]
    lo = ge if ge is not None else prefix.prefixlen
    hi = le if le is not None else 32
    if (ge is not None and not prefix.prefixlen < ge <= 32) or (le is not None and not prefix.prefixlen < le <= 32) or lo > hi:
        raise ParseIssue(f"invalid ge/le for {prefix}")
    return name, PrefixLine(seq, action, prefix, ge, le)


@dataclass
class Iface:
    name: str
    ip: Optional[ipaddress.IPv4Interface] = None
    acl_in: Optional[str] = None
    acl_out: Optional[str] = None
    shutdown: bool = False
    description: str = ""
    ospf_cost: Optional[int] = None
    vrf: Optional[str] = None


@dataclass
class Config:
    hostname: str
    interfaces: Dict[str, Iface] = field(default_factory=dict)
    acls: Dict[str, List[AclLine]] = field(default_factory=dict)
    ospf_networks: List[ipaddress.IPv4Network] = field(default_factory=list)
    prefix_lists: Dict[str, List[PrefixLine]] = field(default_factory=dict)
    vrfs: List[str] = field(default_factory=list)
    ospf_dist_in: Optional[str] = None       # 'distribute-list prefix NAME in' under router ospf


def _fmt_net(n: ipaddress.IPv4Network) -> str:
    if n.prefixlen == 0:
        return "any"
    if n.prefixlen == 32:
        return f"host {n.network_address}"
    return f"{n.network_address} {n.hostmask}"


def _fmt_port(p: Optional[Tuple[int, int]]) -> str:
    if p is None:
        return ""
    return f" eq {p[0]}" if p[0] == p[1] else f" range {p[0]} {p[1]}"


def _wild_to_net(addr: str, wild: str) -> ipaddress.IPv4Network:
    try:
        inv = ipaddress.IPv4Address(int(ipaddress.IPv4Address(wild)) ^ 0xFFFFFFFF)
        return ipaddress.ip_network(f"{addr}/{inv}", strict=False)
    except ValueError as e:
        raise ParseIssue(f"bad address/wildcard '{addr} {wild}': {e}")


def _parse_addr(tok: List[str], i: int) -> Tuple[ipaddress.IPv4Network, int]:
    if i >= len(tok):
        raise ParseIssue("ACL line ends where an address was expected")
    if tok[i] == "any":
        return ANY, i + 1
    if tok[i] == "host":
        if i + 1 >= len(tok):
            raise ParseIssue("'host' without address")
        try:
            return ipaddress.ip_network(f"{tok[i+1]}/32"), i + 2
        except ValueError as e:
            raise ParseIssue(str(e))
    if i + 1 >= len(tok):
        raise ParseIssue("address without wildcard mask")
    return _wild_to_net(tok[i], tok[i + 1]), i + 2


def parse_acl_line(line: str) -> AclLine:
    tok = line.split()
    if len(tok) < 5 or not tok[0].isdigit():
        raise ParseIssue(f"unrecognized ACL line: '{line.strip()}'")
    seq = int(tok[0])
    if tok[1] not in ("permit", "deny"):
        raise ParseIssue(f"unknown ACL action '{tok[1]}'")
    if tok[2] not in PROTOS:
        raise ParseIssue(f"unknown protocol '{tok[2]}'")
    src, i = _parse_addr(tok, 3)
    dst, i = _parse_addr(tok, i)
    dport = None
    rest = tok[i:]
    if rest:
        if tok[2] not in ("tcp", "udp"):
            raise ParseIssue(f"port match not allowed for protocol {tok[2]}")
        if rest[0] == "eq" and len(rest) == 2 and rest[1].isdigit():
            dport = (int(rest[1]), int(rest[1]))
        elif rest[0] == "range" and len(rest) == 3 and rest[1].isdigit() and rest[2].isdigit():
            dport = (int(rest[1]), int(rest[2]))
            if dport[0] > dport[1]:
                raise ParseIssue("port range reversed")
        else:
            raise ParseIssue(f"unrecognized ACL tail: '{' '.join(rest)}'")
    return AclLine(seq, tok[1], tok[2], src, dst, dport)


def parse_config(text: str, filename_stem: Optional[str] = None) -> Config:
    cfg: Optional[Config] = None
    section = None            # ("iface", Iface) | ("acl", name) | ("ospf",)
    for raw in text.splitlines():
        if not raw.strip() or raw.strip() == "!":
            if raw.strip() == "!" and not raw.startswith(" "):
                section = None                # an indented '!' (inside 'vrf definition') keeps the section
            continue
        indented = raw.startswith(" ")
        line = raw.strip()
        if not indented:
            section = None
            if line.startswith("hostname "):
                cfg = Config(hostname=line.split()[1])
            elif cfg is None:
                raise ParseIssue("config must start with 'hostname'")
            elif line.startswith("interface "):
                name = line.split(None, 1)[1]
                if name in cfg.interfaces:
                    raise ParseIssue(f"duplicate interface {name}")
                cfg.interfaces[name] = Iface(name)
                section = ("iface", cfg.interfaces[name])
            elif line.startswith("ip access-list extended "):
                name = line.split()[3]
                if name in cfg.acls:
                    raise ParseIssue(f"duplicate ACL {name}")
                cfg.acls[name] = []
                section = ("acl", name)
            elif line.startswith("ip vrf ") and len(line.split()) == 3:
                name = line.split()[2]
                if name in cfg.vrfs:
                    raise ParseIssue(f"duplicate vrf {name}")
                cfg.vrfs.append(name)
                section = ("vrf", name)
            elif line.startswith("vrf definition ") and len(line.split()) == 3:
                # the newer IOS / IOS-XE form; Batfish accepts it with rd and an ipv4 address family
                # (scripts/diagnose_vrf_batfish.py, variants C and D)
                name = line.split()[2]
                if name in cfg.vrfs:
                    raise ParseIssue(f"duplicate vrf {name}")
                cfg.vrfs.append(name)
                section = ("vrfdef", name)
            elif line.startswith("ip prefix-list "):
                name, pl = parse_prefix_line(line.split()[2:])
                lst = cfg.prefix_lists.setdefault(name, [])
                if any(x.seq == pl.seq for x in lst):
                    raise ParseIssue(f"duplicate sequence {pl.seq} in prefix-list {name}")
                lst.append(pl)
            elif line.startswith("router ospf "):
                section = ("ospf",)
            elif line == "end":
                pass
            else:
                raise ParseIssue(f"unrecognized top-level line: '{line}'")
            continue
        if cfg is None or section is None:
            raise ParseIssue(f"indented line outside a section: '{line}'")
        if section[0] == "iface":
            ifc: Iface = section[1]
            tok = line.split()
            if tok[0] == "description":
                ifc.description = line[len("description"):].strip()
            elif line.startswith("ip address ") and len(tok) == 4:
                try:
                    ifc.ip = ipaddress.IPv4Interface(f"{tok[2]}/{tok[3]}")
                except ValueError as e:
                    raise ParseIssue(f"bad ip address on {ifc.name}: {e}")
            elif line.startswith("ip access-group ") and len(tok) == 4 and tok[3] in ("in", "out"):
                if tok[3] == "in":
                    ifc.acl_in = tok[2]
                else:
                    ifc.acl_out = tok[2]
            elif (line.startswith("ip vrf forwarding ") and len(tok) == 4) or \
                    (line.startswith("vrf forwarding ") and len(tok) == 3):
                if ifc.ip is not None:
                    # IOS deletes the interface address when the VRF is applied after it
                    raise ParseIssue(f"{ifc.name}: '{line}' must come before 'ip address'")
                ifc.vrf = tok[-1]
            elif line.startswith("ip ospf cost "):
                if len(tok) != 4 or not tok[3].isdigit() or not 1 <= int(tok[3]) <= 65535:
                    raise ParseIssue(f"bad OSPF cost on {ifc.name}: '{line}'")
                ifc.ospf_cost = int(tok[3])
            elif line == "shutdown":
                ifc.shutdown = True
            elif line == "no shutdown":
                ifc.shutdown = False
            else:
                raise ParseIssue(f"unrecognized interface line on {ifc.name}: '{line}'")
        elif section[0] == "acl":
            al = parse_acl_line(line)
            if any(x.seq == al.seq for x in cfg.acls[section[1]]):
                raise ParseIssue(f"duplicate sequence {al.seq} in ACL {section[1]}")
            cfg.acls[section[1]].append(al)
        elif section[0] == "vrfdef":
            tok = line.split()
            ok = (tok[0] == "rd" and len(tok) == 2 and re.fullmatch(r"\d+:\d+", tok[1])) or \
                line in ("address-family ipv4", "address-family ipv4 unicast", "exit-address-family")
            if not ok:
                raise ParseIssue(f"unrecognized vrf definition line: '{line}'")
        elif section[0] == "vrf":
            # Batfish (2025.07) reports 'rd' under 'ip vrf' as unrecognized syntax, so it is a parse failure here
            # too: the two judges must fail the same configs (scripts/diagnose_vrf_batfish.py, variant A vs B)
            raise ParseIssue(f"unrecognized vrf line: '{line}'")
        elif section[0] == "ospf":
            tok = line.split()
            if tok[0] == "network" and len(tok) == 5 and tok[3] == "area":
                cfg.ospf_networks.append(_wild_to_net(tok[1], tok[2]))
            elif tok[0] == "distribute-list" and len(tok) == 4 and tok[1] == "prefix" and tok[3] == "in":
                if cfg.ospf_dist_in is not None:
                    raise ParseIssue("more than one inbound distribute-list under router ospf")
                cfg.ospf_dist_in = tok[2]
            else:
                raise ParseIssue(f"unrecognized OSPF line: '{line}'")
    if cfg is None:
        raise ParseIssue("empty config")
    if filename_stem and cfg.hostname != filename_stem:
        raise ParseIssue(f"hostname {cfg.hostname} does not match device {filename_stem}")
    for ifc in cfg.interfaces.values():       # undefined-reference check
        for ref in (ifc.acl_in, ifc.acl_out):
            if ref and ref not in cfg.acls:
                raise ParseIssue(f"{cfg.hostname}:{ifc.name} references undefined ACL {ref}")
    for ifc in cfg.interfaces.values():
        if ifc.vrf and ifc.vrf not in cfg.vrfs:
            raise ParseIssue(f"{cfg.hostname}:{ifc.name} references undefined vrf {ifc.vrf}")
    if cfg.ospf_dist_in and cfg.ospf_dist_in not in cfg.prefix_lists:
        raise ParseIssue(f"{cfg.hostname}: distribute-list references undefined prefix-list {cfg.ospf_dist_in}")
    for name in cfg.prefix_lists:
        cfg.prefix_lists[name].sort(key=lambda l: l.seq)
    for name in cfg.acls:
        cfg.acls[name].sort(key=lambda l: l.seq)
    return cfg


def render_acl_block(name: str, lines: List[AclLine]) -> str:
    out = [f"ip access-list extended {name}"]
    out += [l.render() for l in lines]
    return "\n".join(out) + "\n!\n"


_ACL_BLOCK = re.compile(r"^ip access-list extended (\S+)\n(?:[ ]+.*\n?)*!?\n?", re.M)


def replace_acl_block(text: str, name: str, lines: List[AclLine]) -> str:
    """Replace (or append) one ACL block in a device config text."""
    block = render_acl_block(name, lines)
    pat = re.compile(rf"^ip access-list extended {re.escape(name)}\n(?:[ ]+.*\n)*!\n?", re.M)
    if pat.search(text):
        return pat.sub(lambda m: block, text, count=1)
    return text.rstrip("\n") + "\n!\n" + block


def replace_prefix_list(text: str, name: str, lines: List[PrefixLine]) -> str:
    """Replace (or append) one prefix-list in a device config text."""
    block = "\n".join(l.render(name) for l in lines) + "\n!\n"
    pat = re.compile(rf"(?:^ip prefix-list {re.escape(name)} seq .*\n)+!\n?", re.M)
    if pat.search(text):
        return pat.sub(lambda m: block, text, count=1)
    return text.rstrip("\n") + "\n!\n" + block


def bind_ospf_distribute_list(text: str, name: str) -> str:
    """Add 'distribute-list prefix NAME in' at the end of the router ospf block (no-op if already bound)."""
    line = f" distribute-list prefix {name} in\n"
    if line in text:
        return text
    m = re.search(r"^router ospf \d+\n((?: .*\n)*)", text, re.M)
    if not m:
        raise ParseIssue("no router ospf block to bind the distribute-list to")
    return text[:m.end()] + line + text[m.end():]


def set_interface_ospf_cost(text: str, ifname: str, cost: Optional[int]) -> str:
    """Set (or with cost=None remove) 'ip ospf cost N' inside one interface block; inserted after 'ip address'."""
    m = re.search(rf"^interface {re.escape(ifname)}\n((?: .*\n)*)", text, re.M)
    if not m:
        raise ParseIssue(f"no interface {ifname}")
    body = re.sub(r"^ ip ospf cost .*\n", "", m.group(1), flags=re.M)
    if cost is not None:
        lines = body.splitlines(keepends=True)
        at = next((i + 1 for i, l in enumerate(lines) if l.startswith(" ip address ")), len(lines))
        lines.insert(at, f" ip ospf cost {cost}\n")
        body = "".join(lines)
    return text[:m.start(1)] + body + text[m.end(1):]


def interface_by_description(text: str, pred) -> str:
    for m in re.finditer(r"^interface (\S+)\n description (.*)\n", text, re.M):
        if pred(m.group(2)):
            return m.group(1)
    raise ParseIssue("no matching interface")


def set_interface_vrf(text: str, ifname: str, vrf: str) -> str:
    """Put an interface into a VRF: 'ip vrf forwarding' goes right before 'ip address' (IOS order)."""
    m = re.search(rf"^interface {re.escape(ifname)}\n((?: .*\n)*)", text, re.M)
    if not m:
        raise ParseIssue(f"no interface {ifname}")
    body = re.sub(r"^ ip vrf forwarding .*\n", "", m.group(1), flags=re.M)
    lines = body.splitlines(keepends=True)
    at = next((i for i, l in enumerate(lines) if l.startswith(" ip address ")), len(lines))
    lines.insert(at, f" ip vrf forwarding {vrf}\n")
    return text[:m.start(1)] + "".join(lines) + text[m.end(1):]


def ensure_vrf(text: str, vrf: str, rd: Optional[str] = None) -> str:
    """Define a VRF-lite VRF ('ip vrf NAME', no route distinguisher: none is needed without MPLS VPNs)."""
    if re.search(rf"^ip vrf {re.escape(vrf)}$", text, re.M):
        return text
    return text.rstrip("\n") + f"\n!\nip vrf {vrf}\n!\n"
