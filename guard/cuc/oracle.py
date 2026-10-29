"""Oracle test suite: 30 hand-made changes with human-derived expected outcomes.

The expected set of broken invariants for each case was reasoned out BY HAND
from the baseline ACLs (see topology.py) -- it is NOT produced by running the
checker.  The checker is trusted only if it agrees with all 30.
Labels: safe (nothing breaks), breaking (clearly breaks), subtle (order /
overlap / routing effects that are easy to get wrong).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, List, Set

from .edits import Texts, add_acl_line, remove_acl_line, sub, acl_lines, set_acl
from .iosmodel import AclLine

S1, S2, S3, S4 = ("10.1.1.0 0.0.0.255", "10.1.2.0 0.0.0.255", "10.1.3.0 0.0.0.255", "10.1.4.0 0.0.0.255")


@dataclass
class Case:
    id: str
    label: str                       # safe | breaking | subtle
    why: str                         # the human reasoning
    edit: Callable[[Texts], Texts]
    expected_broken: Set[str]


def _renumber(t: Texts, dev: str, acl: str, old: int, new: int) -> Texts:
    ls = [AclLine(new, l.action, l.proto, l.src, l.dst, l.dport) if l.seq == old else l for l in acl_lines(t[dev], dev, acl)]
    return set_acl(t, dev, acl, ls)


def build_cases() -> List[Case]:
    C: List[Case] = []
    add = lambda *a: C.append(Case(*a))
    # ---------------- SAFE ----------------
    add("S01", "safe", "permit tcp 443 E4->E1; invariant I06 only covers tcp/22",
        lambda t: add_acl_line(t, "E1", "TO_E1", f" 30 permit tcp {S4} {S1} eq 443"), set())
    add("S02", "safe", "permit tcp 443 E3->E2; I04 only covers tcp/80",
        lambda t: add_acl_line(t, "E2", "TO_E2", f" 20 permit tcp {S3} {S2} eq 443"), set())
    add("S03", "safe", "permit tcp 443 E2->E4 at the firewall; I05 only covers tcp/80",
        lambda t: add_acl_line(t, "FW", "TO_E4", f" 30 permit tcp {S2} {S4} eq 443"), set())
    add("S04", "safe", "deny tcp 3306 E1->E3 before the catch-all; R04/R06 use 443/80",
        lambda t: add_acl_line(t, "E3", "TO_E3", f" 25 deny tcp {S1} {S3} eq 3306"), set())
    add("S05", "safe", "deny udp 53 E2->E3; no invariant uses udp from E2",
        lambda t: add_acl_line(t, "E3", "TO_E3", f" 25 deny udp {S2} {S3} eq 53"), set())
    add("S06", "safe", "interface description change only",
        lambda t: sub(t, "E1", " description hosts", " description hosts-lan"), set())
    add("S07", "safe", "explicit deny that duplicates the implicit deny (E2->E1 tcp/22)",
        lambda t: add_acl_line(t, "E1", "TO_E1", f" 30 deny tcp {S2} {S1} eq 22"), set())
    add("S08", "safe", "new ACL defined but attached nowhere",
        lambda t: add_acl_line(t, "R1", "UNUSED", f" 10 deny ip {S2} {S1}"), set())
    add("S09", "safe", "deny tcp 8080 E1->E3 before the catch-all",
        lambda t: add_acl_line(t, "E3", "TO_E3", f" 25 deny tcp {S1} {S3} eq 8080"), set())
    add("S10", "safe", "permit icmp E2->E1; I03 only covers tcp/22",
        lambda t: add_acl_line(t, "E1", "TO_E1", f" 30 permit icmp {S2} {S1}"), set())
    # ---------------- BREAKING ----------------
    add("B01", "breaking", "removing 'permit ip any any' closes TO_E3: E1->E3 443/80 and E2->E3 443 die",
        lambda t: remove_acl_line(t, "E3", "TO_E3", 30), {"R04", "R05", "R06"})
    add("B02", "breaking", "removing TO_E1 line 10 kills E3->E1 tcp/22",
        lambda t: remove_acl_line(t, "E1", "TO_E1", 10), {"R01"})
    add("B03", "breaking", "removing TO_E4 line 10 kills E1->E4 tcp/80 (and its waypoint check)",
        lambda t: remove_acl_line(t, "FW", "TO_E4", 10), {"R07", "W01"})
    add("B04", "breaking", "shutting E3's uplink isolates E3: every flow from/to E3 breaks",
        lambda t: sub(t, "E3", " ip address 10.0.0.26 255.255.255.252\n no shutdown", " ip address 10.0.0.26 255.255.255.252\n shutdown"),
        {"R01", "R04", "R05", "R06", "R08"})
    add("B05", "breaking", "line 20 flipped to permit: E4 may reach E3 again",
        lambda t: sub(t, "E3", f" 20 deny ip {S4} {S3}", f" 20 permit ip {S4} {S3}"), {"I02"})
    add("B06", "breaking", "deleting 'deny tcp 22 E2->E3' lets the catch-all permit it",
        lambda t: remove_acl_line(t, "E3", "TO_E3", 10), {"I01"})
    add("B07", "breaking", "permit ip any any at top of TO_E1 opens E1 to everyone",
        lambda t: add_acl_line(t, "E1", "TO_E1", " 5 permit ip any any"), {"I03", "I06"})
    add("B08", "breaking", "E1's host subnet no longer advertised: nobody can route TO E1 (E1->* still works)",
        lambda t: sub(t, "E1", " network 10.1.1.0 0.0.0.255 area 0\n", ""), {"R01", "R02"})
    add("B09", "breaking", "removing the ACL binding on FW's E4 port opens E4: E2->E4 tcp/80 leaks",
        lambda t: sub(t, "FW", " ip access-group TO_E4 out\n", ""), {"I05"})
    add("B10", "breaking", "R1 stops running OSPF toward E1: E1 is cut off from the network",
        lambda t: sub(t, "R1", " network 10.0.0.16 0.0.0.3 area 0\n", ""),
        {"R01", "R02", "R03", "R04", "R06", "R07", "W01"})
    # ---------------- SUBTLE ----------------
    add("U01", "subtle", "permit tcp 20-30 E2->E3 placed ABOVE the tcp/22 deny shadows it",
        lambda t: add_acl_line(t, "E3", "TO_E3", f" 5 permit tcp {S2} {S3} range 20 30"), {"I01"})
    add("U02", "subtle", "deny tcp 1-1000 E1->E3 placed at top swallows the 443 and 80 permits",
        lambda t: add_acl_line(t, "E3", "TO_E3", f" 5 deny tcp {S1} {S3} range 1 1000"), {"R04", "R06"})
    add("U03", "subtle", "deny tcp 1-100 E3->E1 at top kills tcp/22 permit (line 10)",
        lambda t: add_acl_line(t, "E1", "TO_E1", f" 5 deny tcp {S3} {S1} range 1 100"), {"R01"})
    add("U04", "subtle", "narrowing 'deny ip' to 'deny tcp 1-1000' lets 8080, udp and icmp through",
        lambda t: sub(t, "E3", f" 20 deny ip {S4} {S3}", f" 20 deny tcp {S4} {S3} range 1 1000"), {"I02"})
    add("U05", "subtle", "widening permit tcp/80 to range 80-8080 on E1->E4 breaks nothing",
        lambda t: sub(t, "FW", f" 10 permit tcp {S1} {S4} eq 80", f" 10 permit tcp {S1} {S4} range 80 8080"), set())
    add("U06", "subtle", "permit tcp 20-30 E4->E1 at top covers tcp/22 -> E4 leaks into E1",
        lambda t: add_acl_line(t, "E1", "TO_E1", f" 5 permit tcp {S4} {S1} range 20 30"), {"I06"})
    add("U07", "subtle", "ACL bound inbound instead of outbound: traffic INTO E3 is no longer filtered",
        lambda t: sub(t, "E3", " ip access-group TO_E3 out", " ip access-group TO_E3 in"), {"I01", "I02"})
    add("U08", "subtle", "catch-all permit renumbered 30->15 sits above 'deny ip E4->E3'; tcp/22 deny at 10 still wins",
        lambda t: _renumber(t, "E3", "TO_E3", 30, 15), {"I02"})
    add("U09", "subtle", "swapping the order of two disjoint deny lines changes nothing",
        lambda t: _renumber(_renumber(_renumber(t, "E3", "TO_E3", 10, 99), "E3", "TO_E3", 20, 10), "E3", "TO_E3", 99, 20), set())
    add("U10", "subtle", "dropping one ring link: traffic reroutes the long way, nothing breaks",
        lambda t: sub(t, "R2", " network 10.0.0.0 0.0.0.3 area 0\n", ""), set())
    return C
