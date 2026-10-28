"""Independent packet-tracing verification oracle for Phase 2 audit.

Completely written from scratch without importing or referencing generator.oracle.reference.
Implements:
- Longest-prefix-match (LPM) static routing with next-hop resolution
- Per-interface VRF assignment and cross-VRF boundary isolation
- Extended Cisco ACL parsing and first-match rule evaluation
- Explicit ports, protocols (TCP, UDP, IP), wildcards, and implicit deny
- Hop-by-hop forwarding simulation with loop prevention
- Waypoint node traversal enforcement
"""

from dataclasses import dataclass, field
import ipaddress
import re
from typing import Any

from agent.contract.models import Invariant, InvariantKind


@dataclass
class AclRule:
    action: str  # "permit" or "deny"
    proto: str   # "ip", "tcp", "udp"
    src_net: ipaddress.IPv4Network | None
    src_wildcard: int | None
    src_ip_int: int | None
    src_port: int | None
    dst_net: ipaddress.IPv4Network | None
    dst_wildcard: int | None
    dst_ip_int: int | None
    dst_port: int | None
    line: str

    def matches(self, packet_proto: str, packet_src_ip: int, packet_dst_ip: int, packet_dst_port: int | None) -> bool:
        # 1. Protocol check
        if self.proto != "ip" and self.proto != packet_proto:
            return False

        # 2. Source IP check
        if self.src_wildcard is not None and self.src_ip_int is not None:
            # wildcard match: (ip & ~wildcard) == (rule_ip & ~wildcard)
            mask = ~self.src_wildcard & 0xFFFFFFFF
            if (packet_src_ip & mask) != (self.src_ip_int & mask):
                return False

        # 3. Destination IP check
        if self.dst_wildcard is not None and self.dst_ip_int is not None:
            mask = ~self.dst_wildcard & 0xFFFFFFFF
            if (packet_dst_ip & mask) != (self.dst_ip_int & mask):
                return False

        # 4. Destination port check
        if self.dst_port is not None:
            if packet_dst_port is None or packet_dst_port != self.dst_port:
                return False

        return True


@dataclass
class InterfaceConfig:
    name: str
    ip: ipaddress.IPv4Address | None = None
    netmask: ipaddress.IPv4Address | None = None
    network: ipaddress.IPv4Network | None = None
    vrf: str | None = None
    acl_in: str | None = None
    acl_out: str | None = None


@dataclass
class StaticRoute:
    vrf: str | None
    prefix: ipaddress.IPv4Network
    next_hop: ipaddress.IPv4Address


@dataclass
class ParsedNode:
    hostname: str
    interfaces: dict[str, InterfaceConfig] = field(default_factory=dict)
    static_routes: list[StaticRoute] = field(default_factory=list)
    acls: dict[str, list[AclRule]] = field(default_factory=dict)


def parse_acl_rule(line_text: str) -> AclRule | None:
    raw = line_text.strip()
    if not (raw.startswith("permit ") or raw.startswith("deny ")):
        return None

    tokens = raw.split()
    action = tokens[0].lower()
    proto = tokens[1].lower()

    idx = 2
    # Parse source
    src_wildcard = None
    src_ip_int = None
    src_port = None

    if tokens[idx].lower() == "any":
        idx += 1
    elif "/" in tokens[idx]:
        net = ipaddress.IPv4Network(tokens[idx], strict=False)
        src_ip_int = int(net.network_address)
        src_wildcard = int(net.hostmask)
        idx += 1
    elif tokens[idx].lower() == "host":
        src_ip = ipaddress.IPv4Address(tokens[idx + 1])
        src_ip_int = int(src_ip)
        src_wildcard = 0
        idx += 2
    else:
        src_ip = ipaddress.IPv4Address(tokens[idx])
        src_wildcard = int(ipaddress.IPv4Address(tokens[idx + 1]))
        src_ip_int = int(src_ip)
        idx += 2

    # Optional src port
    if idx < len(tokens) and tokens[idx].lower() == "eq":
        src_port = int(tokens[idx + 1])
        idx += 2

    # Parse destination
    dst_wildcard = None
    dst_ip_int = None
    dst_port = None

    if idx < len(tokens):
        if tokens[idx].lower() == "any":
            idx += 1
        elif "/" in tokens[idx]:
            net = ipaddress.IPv4Network(tokens[idx], strict=False)
            dst_ip_int = int(net.network_address)
            dst_wildcard = int(net.hostmask)
            idx += 1
        elif tokens[idx].lower() == "host":
            dst_ip = ipaddress.IPv4Address(tokens[idx + 1])
            dst_ip_int = int(dst_ip)
            dst_wildcard = 0
            idx += 2
        else:
            dst_ip = ipaddress.IPv4Address(tokens[idx])
            dst_wildcard = int(ipaddress.IPv4Address(tokens[idx + 1]))
            dst_ip_int = int(dst_ip)
            idx += 2

    # Optional dst port
    if idx < len(tokens) and tokens[idx].lower() == "eq":
        dst_port = int(tokens[idx + 1])
        idx += 2

    return AclRule(
        action=action,
        proto=proto,
        src_net=None,
        src_wildcard=src_wildcard,
        src_ip_int=src_ip_int,
        src_port=src_port,
        dst_net=None,
        dst_wildcard=dst_wildcard,
        dst_ip_int=dst_ip_int,
        dst_port=dst_port,
        line=raw,
    )


def parse_cisco_config(hostname_key: str, config_text: str) -> ParsedNode:
    node = ParsedNode(hostname=hostname_key)
    current_intf: InterfaceConfig | None = None
    current_acl_name: str | None = None

    for raw_line in config_text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("!"):
            continue

        if line.startswith("hostname "):
            node.hostname = line.split()[1]
            continue

        if line.startswith("interface "):
            intf_name = line.split()[1]
            current_intf = InterfaceConfig(name=intf_name)
            node.interfaces[intf_name] = current_intf
            current_acl_name = None
            continue

        if current_intf is not None:
            if line.startswith("ip vrf forwarding "):
                current_intf.vrf = line.split()[3]
                continue
            if line.startswith("ip address "):
                parts = line.split()
                ip_addr = ipaddress.IPv4Address(parts[2])
                net_mask = ipaddress.IPv4Address(parts[3])
                current_intf.ip = ip_addr
                current_intf.netmask = net_mask
                current_intf.network = ipaddress.IPv4Network(f"{parts[2]}/{parts[3]}", strict=False)
                continue
            if line.startswith("ip access-group "):
                parts = line.split()
                acl_name = parts[2]
                direction = parts[3].lower()
                if direction == "in":
                    current_intf.acl_in = acl_name
                elif direction == "out":
                    current_intf.acl_out = acl_name
                continue

        if line.startswith("ip access-list extended "):
            current_acl_name = line.split()[3]
            node.acls.setdefault(current_acl_name, [])
            current_intf = None
            continue

        if current_acl_name is not None and (line.startswith("permit ") or line.startswith("deny ")):
            rule = parse_acl_rule(line)
            if rule:
                node.acls[current_acl_name].append(rule)
            continue

        if line.startswith("ip route "):
            # ip route [vrf <vrf>] <prefix> <mask> <next_hop>
            parts = line.split()
            if len(parts) >= 6 and parts[2].lower() == "vrf":
                vrf = parts[3]
                pfx = parts[4]
                msk = parts[5]
                nh = parts[6]
            elif len(parts) >= 4:
                vrf = None
                pfx = parts[2]
                msk = parts[3]
                nh = parts[4]
            else:
                continue
            try:
                route_net = ipaddress.IPv4Network(f"{pfx}/{msk}", strict=False)
                nh_ip = ipaddress.IPv4Address(nh)
                node.static_routes.append(StaticRoute(vrf=vrf, prefix=route_net, next_hop=nh_ip))
            except Exception:
                pass

    return node


class IndependentAuditChecker:
    """Independent packet tracer verifying reachability, isolation, and waypoints."""

    def __init__(self):
        pass

    def check(self, configs: dict[str, str], invariants: list[Invariant]) -> dict[str, bool]:
        nodes = {name: parse_cisco_config(name, cfg) for name, cfg in configs.items()}
        results = {}
        for inv in invariants:
            results[inv.id] = self._evaluate_invariant(nodes, inv)
        return results

    def _evaluate_invariant(self, nodes: dict[str, ParsedNode], inv: Invariant) -> bool:
        src_ip = ipaddress.IPv4Address(inv.source.split("/")[0])
        dst_ip = ipaddress.IPv4Address(inv.destination.split("/")[0])
        src_ip_int = int(src_ip)
        dst_ip_int = int(dst_ip)
        proto = str(inv.headers.get("protocol", "tcp")).lower()
        dst_port = inv.headers.get("dst_port", 80)
        req_waypoint = inv.via or inv.headers.get("waypoint")

        # 1. Locate ingress node and interface
        ingress_node: ParsedNode | None = None
        ingress_intf: InterfaceConfig | None = None

        for node in nodes.values():
            for intf in node.interfaces.values():
                if intf.network and src_ip in intf.network:
                    ingress_node = node
                    ingress_intf = intf
                    break
            if ingress_node:
                break

        if not ingress_node or not ingress_intf:
            # Source not located on any subnet
            delivered = False
            visited = []
        else:
            delivered, visited = self._trace_path(
                nodes=nodes,
                start_node=ingress_node,
                start_intf=ingress_intf,
                src_ip_int=src_ip_int,
                dst_ip=dst_ip,
                dst_ip_int=dst_ip_int,
                proto=proto,
                dst_port=dst_port,
            )

        # Invariant kind verification
        if inv.kind == InvariantKind.REACH:
            if not delivered:
                return False
            if req_waypoint and req_waypoint not in visited:
                return False
            return True

        elif inv.kind == InvariantKind.ISOLATE:
            # Must NOT be delivered
            return not delivered

        elif inv.kind == InvariantKind.WAYPOINT:
            if not delivered:
                return False
            if req_waypoint and req_waypoint not in visited:
                return False
            return True

        return False

    def _trace_path(
        self,
        nodes: dict[str, ParsedNode],
        start_node: ParsedNode,
        start_intf: InterfaceConfig,
        src_ip_int: int,
        dst_ip: ipaddress.IPv4Address,
        dst_ip_int: int,
        proto: str,
        dst_port: int | None,
    ) -> tuple[bool, list[str]]:
        current_node = start_node
        current_vrf = start_intf.vrf
        visited = [current_node.hostname]

        # Check inbound ACL on initial interface
        if start_intf.acl_in and start_intf.acl_in in current_node.acls:
            if not self._eval_acl(current_node.acls[start_intf.acl_in], proto, src_ip_int, dst_ip_int, dst_port):
                return False, visited

        hops = 0
        max_hops = 64

        while hops < max_hops:
            hops += 1

            # Check if destination IP is in a directly connected subnet on current_node
            for intf in current_node.interfaces.values():
                if intf.network and dst_ip in intf.network:
                    # Target directly connected! Verify VRF match
                    if intf.vrf == current_vrf:
                        # Delivered!
                        return True, visited
                    else:
                        # Dropped at VRF boundary
                        return False, visited

            # Longest prefix match static route lookup
            best_route: StaticRoute | None = None
            best_pfx_len = -1

            for route in current_node.static_routes:
                if route.vrf == current_vrf and dst_ip in route.prefix:
                    if route.prefix.prefixlen > best_pfx_len:
                        best_pfx_len = route.prefix.prefixlen
                        best_route = route

            if best_route is None:
                # No route
                return False, visited

            next_hop = best_route.next_hop

            # Find egress interface for next_hop
            egress_intf: InterfaceConfig | None = None
            for intf in current_node.interfaces.values():
                if intf.network and next_hop in intf.network:
                    egress_intf = intf
                    break

            if egress_intf is None:
                # Next hop unresolvable
                return False, visited

            # Egress ACL check
            if egress_intf.acl_out and egress_intf.acl_out in current_node.acls:
                if not self._eval_acl(current_node.acls[egress_intf.acl_out], proto, src_ip_int, dst_ip_int, dst_port):
                    return False, visited

            # Find next hop device and ingress interface
            next_node: ParsedNode | None = None
            next_intf: InterfaceConfig | None = None

            for node in nodes.values():
                for intf in node.interfaces.values():
                    if intf.ip == next_hop:
                        next_node = node
                        next_intf = intf
                        break
                if next_node:
                    break

            if not next_node or not next_intf:
                # Unconnected next hop
                return False, visited

            # Ingress ACL check on next node
            if next_intf.acl_in and next_intf.acl_in in next_node.acls:
                if not self._eval_acl(next_node.acls[next_intf.acl_in], proto, src_ip_int, dst_ip_int, dst_port):
                    return False, visited

            # Advance to next node
            current_node = next_node
            current_vrf = next_intf.vrf
            visited.append(current_node.hostname)

        # Max hops exceeded (routing loop)
        return False, visited

    def _eval_acl(self, rules: list[AclRule], proto: str, src_ip_int: int, dst_ip_int: int, dst_port: int | None) -> bool:
        for rule in rules:
            if rule.matches(proto, src_ip_int, dst_ip_int, dst_port):
                return rule.action == "permit"
        # Implicit deny
        return False
