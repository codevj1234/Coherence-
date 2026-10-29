"""Small test network and baseline config generator (Phase 1, test bed).

        E1(10.1.1/24)   E2(10.1.2/24)   E3(10.1.3/24)
           |               |               |
          R1 ------------ R2 ------------ R3
           |  \\                            |
           |   ------------------------   |
          R4 ----------------------------+
           |
          FW   (the only way to E4 is through FW: the waypoint)
           |
        E4(10.1.4/24)

Core ring R1-R2-R3-R4-R1.  Every link is a /30 from 10.0.0.0/16.
Edge routers own one /24 host subnet each.  OSPF everywhere.
Policy (ACLs on the host-facing interface, direction 'out'):
  TO_E1, TO_E2, TO_E4(on FW): closed policy (implicit deny at the end)
  TO_E3: open policy (explicit denies, then 'permit ip any any')
"""
from __future__ import annotations

import os
from typing import Dict, List, Tuple

CORE_LINKS: List[Tuple[str, str]] = [
    ("R1", "R2"), ("R2", "R3"), ("R3", "R4"), ("R4", "R1"),   # ring
    ("R1", "E1"), ("R2", "E2"), ("R3", "E3"), ("R4", "FW"), ("FW", "E4"),
]
HOST_SUBNETS = {"E1": "10.1.1", "E2": "10.1.2", "E3": "10.1.3", "E4": "10.1.4"}
DEVICES = ["R1", "R2", "R3", "R4", "FW", "E1", "E2", "E3", "E4"]

# Which ACL guards which host subnet, and on which device/interface it sits.
ACL_HOME = {
    "E1": ("E1", "TO_E1"),
    "E2": ("E2", "TO_E2"),
    "E3": ("E3", "TO_E3"),
    "E4": ("FW", "TO_E4"),     # the firewall guards E4 on its E4-facing interface
}

W24 = "0.0.0.255"


def _acl_baseline() -> Dict[str, List[str]]:
    s = lambda n: f"10.1.{n}.0 {W24}"
    return {
        "TO_E1": [
            f" 10 permit tcp {s(3)} {s(1)} eq 22",
            f" 20 permit tcp {s(2)} {s(1)} eq 80",
        ],
        "TO_E2": [
            f" 10 permit tcp {s(1)} {s(2)} eq 80",
        ],
        "TO_E3": [
            f" 10 deny tcp {s(2)} {s(3)} eq 22",
            f" 20 deny ip {s(4)} {s(3)}",
            " 30 permit ip any any",
        ],
        "TO_E4": [
            f" 10 permit tcp {s(1)} {s(4)} eq 80",
            f" 20 permit tcp {s(3)} {s(4)} eq 443",
        ],
    }


def build_baseline() -> Dict[str, str]:
    """Return {device: config text} for the baseline network."""
    ifaces: Dict[str, List[dict]] = {d: [] for d in DEVICES}
    link_no = 0
    for a, b in CORE_LINKS:
        base = 4 * link_no
        link_no += 1
        net = f"10.0.{base // 256}.{base % 256}"
        ia, ib = f"10.0.{base // 256}.{base % 256 + 1}", f"10.0.{base // 256}.{base % 256 + 2}"
        ifaces[a].append(dict(ip=ia, mask="255.255.255.252", desc=f"to {b}", net=net, wild="0.0.0.3"))
        ifaces[b].append(dict(ip=ib, mask="255.255.255.252", desc=f"to {a}", net=net, wild="0.0.0.3"))
    for dev, pre in HOST_SUBNETS.items():
        ifaces[dev].append(dict(ip=f"{pre}.1", mask="255.255.255.0", desc="hosts", net=f"{pre}.0",
                                wild=W24, host=True))
    acls = _acl_baseline()
    out: Dict[str, str] = {}
    for dev in DEVICES:
        lines = [f"hostname {dev}", "!"]
        for n, itf in enumerate(ifaces[dev]):
            lines += [f"interface GigabitEthernet0/{n}", f" description {itf['desc']}",
                      f" ip address {itf['ip']} {itf['mask']}"]
            for host_owner, (acl_dev, acl_name) in ACL_HOME.items():
                if itf.get("host") and dev == host_owner and acl_dev == dev:
                    lines.append(f" ip access-group {acl_name} out")
            if dev == "FW" and itf["desc"] == "to E4":
                lines.append(" ip access-group TO_E4 out")
            lines += [" no shutdown", "!"]
        lines.append("router ospf 1")
        for itf in ifaces[dev]:
            lines.append(f" network {itf['net']} {itf['wild']} area 0")
        lines.append("!")
        for acl_dev, acl_name in ACL_HOME.values():
            if acl_dev == dev:
                lines.append(f"ip access-list extended {acl_name}")
                lines += acls[acl_name]
                lines.append("!")
        out[dev] = "\n".join(lines) + "\n"
    return out


def write_snapshot(configs: Dict[str, str], directory: str) -> None:
    cfgdir = os.path.join(directory, "configs")
    os.makedirs(cfgdir, exist_ok=True)
    for dev, text in configs.items():
        with open(os.path.join(cfgdir, f"{dev}.cfg"), "w") as f:
            f.write(text)


def read_snapshot(directory: str) -> Dict[str, str]:
    cfgdir = os.path.join(directory, "configs")
    return {fn[:-4]: open(os.path.join(cfgdir, fn)).read()
            for fn in sorted(os.listdir(cfgdir)) if fn.endswith(".cfg")}
