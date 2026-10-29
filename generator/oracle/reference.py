"""Pure-Python reference verification oracle.

Simulates packet traversal across Cisco IOS network configurations modeling:
- Interface IP addresses, subnets, and VRF assignments
- Named extended ACLs (permit/deny, protocols, wildcard masks, port operators, implicit deny)
- Inbound and outbound access-group enforcement
- Static routes with longest prefix match and VRF scoping
- Point-to-point and multi-hop forwarding
- Invariant evaluation for reachability, VRF/ACL isolation, and waypoint traversal
"""

from dataclasses import dataclass, field
import ipaddress
import re
from agent.contract.models import Invariant, InvariantKind
from generator.oracle.base import FailureDetail, Oracle


@dataclass(frozen=True)
class ACLRule:
    """Parsed single line from an extended access-list."""

    line_no: int
    action: str  # "permit" or "deny"
    protocol: str  # "ip", "tcp", "udp", "icmp"
    src_network: ipaddress.IPv4Network | None = None  # None means 'any'
    dst_network: ipaddress.IPv4Network | None = None  # None means 'any'
    src_wildcard: int | None = None
    src_ip_int: int | None = None
    dst_wildcard: int | None = None
    dst_ip_int: int | None = None
    dst_port_op: str | None = None  # e.g. "eq", "range"
    dst_port: int | None = None
    raw_text: str = ""

    def matches(self, packet_proto: str, src_ip: ipaddress.IPv4Address, dst_ip: ipaddress.IPv4Address, dst_port: int | None) -> bool:
        """Check if packet matches this ACL rule."""
        # 1. Protocol check: 'ip' matches all IP protocols
        if self.protocol != "ip" and self.protocol != packet_proto:
            return False

        # 2. Source check
        if self.src_wildcard is not None and self.src_ip_int is not None:
            mask = ~self.src_wildcard & 0xFFFFFFFF
            if (int(src_ip) & mask) != (self.src_ip_int & mask):
                return False
        elif self.src_network is not None and src_ip not in self.src_network:
            return False

        # 3. Destination check
        if self.dst_wildcard is not None and self.dst_ip_int is not None:
            mask = ~self.dst_wildcard & 0xFFFFFFFF
            if (int(dst_ip) & mask) != (self.dst_ip_int & mask):
                return False
        elif self.dst_network is not None and dst_ip not in self.dst_network:
            return False

        # 4. Port check (only applies if rule specifies port)
        if self.dst_port_op == "eq" and self.dst_port is not None:
            if dst_port != self.dst_port:
                return False

        return True


@dataclass
class ParsedInterface:
    name: str
    description: str = ""
    ip_address: ipaddress.IPv4Address | None = None
    network: ipaddress.IPv4Network | None = None
    vrf: str | None = None
    acl_in: str | None = None
    acl_out: str | None = None


@dataclass
class ParsedStaticRoute:
    vrf: str | None
    network: ipaddress.IPv4Network
    next_hop: ipaddress.IPv4Address


@dataclass
class DeviceConfigModel:
    name: str
    interfaces: dict[str, ParsedInterface] = field(default_factory=dict)
    acls: dict[str, list[ACLRule]] = field(default_factory=dict)
    static_routes: list[ParsedStaticRoute] = field(default_factory=list)
    vrfs: set[str] = field(default_factory=set)


def _parse_wildcard(ip_str: str, mask_str: str) -> ipaddress.IPv4Network:
    """Convert an IP and Cisco wildcard (or netmask) to an IPv4Network."""
    ip_int = int(ipaddress.IPv4Address(ip_str))
    mask_int = int(ipaddress.IPv4Address(mask_str))

    # Detect if mask_str is a wildcard (e.g. 0.0.255.255) vs netmask (255.255.0.0)
    if mask_int == 0xFFFFFFFF:
        # 255.255.255.255 wildcard matches any (0.0.0.0/0)
        return ipaddress.IPv4Network("0.0.0.0/0")
    if mask_int == 0:
        # 0.0.0.0 wildcard matches exact host (/32)
        return ipaddress.IPv4Network(f"{ip_str}/32")

    # If it's a standard Cisco inverse wildcard mask (e.g. 0.0.0.255)
    inverted = 0xFFFFFFFF ^ mask_int
    prefixlen = bin(inverted).count("1")
    return ipaddress.IPv4Network((ip_int & inverted, prefixlen), strict=False)


def _parse_acl_line(line: str, line_no: int) -> ACLRule | None:
    """Parse a single extended ACL rule line."""
    tokens = line.strip().split()
    if tokens and tokens[0].isdigit():
        # IOS sequence number ("10 permit tcp ..."), as written by most models and by 'show running-config':
        # without this the whole line was dropped, so a numbered permit or deny silently did nothing
        tokens = tokens[1:]
    if len(tokens) < 4:
        return None

    action = tokens[0].lower()  # permit or deny
    if action not in ("permit", "deny"):
        return None

    proto = tokens[1].lower()  # ip, tcp, udp, icmp

    idx = 2
    # Source
    src_net: ipaddress.IPv4Network | None = None
    src_wildcard: int | None = None
    src_ip_int: int | None = None
    if tokens[idx].lower() == "any":
        src_net = None
        idx += 1
    elif "/" in tokens[idx]:
        src_net = ipaddress.IPv4Network(tokens[idx], strict=False)
        src_ip_int = int(src_net.network_address)
        src_wildcard = int(src_net.hostmask)
        idx += 1
    elif tokens[idx].lower() == "host":
        src_ip = ipaddress.IPv4Address(tokens[idx+1])
        src_ip_int = int(src_ip)
        src_wildcard = 0
        src_net = ipaddress.IPv4Network(f"{tokens[idx+1]}/32")
        idx += 2
    elif idx + 1 < len(tokens):
        # ip + wildcard
        try:
            src_ip = ipaddress.IPv4Address(tokens[idx])
            src_wildcard = int(ipaddress.IPv4Address(tokens[idx+1]))
            src_ip_int = int(src_ip)
            src_net = _parse_wildcard(tokens[idx], tokens[idx+1])
            idx += 2
        except Exception:
            return None
    else:
        return None

    # Destination
    if idx >= len(tokens):
        return None

    dst_net: ipaddress.IPv4Network | None = None
    dst_wildcard: int | None = None
    dst_ip_int: int | None = None
    if tokens[idx].lower() == "any":
        dst_net = None
        idx += 1
    elif "/" in tokens[idx]:
        dst_net = ipaddress.IPv4Network(tokens[idx], strict=False)
        dst_ip_int = int(dst_net.network_address)
        dst_wildcard = int(dst_net.hostmask)
        idx += 1
    elif tokens[idx].lower() == "host":
        dst_ip = ipaddress.IPv4Address(tokens[idx+1])
        dst_ip_int = int(dst_ip)
        dst_wildcard = 0
        dst_net = ipaddress.IPv4Network(f"{tokens[idx+1]}/32")
        idx += 2
    elif idx + 1 < len(tokens):
        try:
            dst_ip = ipaddress.IPv4Address(tokens[idx])
            dst_wildcard = int(ipaddress.IPv4Address(tokens[idx+1]))
            dst_ip_int = int(dst_ip)
            dst_net = _parse_wildcard(tokens[idx], tokens[idx+1])
            idx += 2
        except Exception:
            return None
    else:
        return None

    # Port operator (optional)
    port_op: str | None = None
    port_val: int | None = None

    if idx < len(tokens):
        if tokens[idx].lower() == "eq" and idx + 1 < len(tokens):
            port_op = "eq"
            port_str = tokens[idx+1]
            if port_str.isdigit():
                port_val = int(port_str)
            else:
                port_map = {"http": 80, "https": 443, "domain": 53, "telnet": 23, "ssh": 22}
                port_val = port_map.get(port_str.lower(), None)

    return ACLRule(
        line_no=line_no,
        action=action,
        protocol=proto,
        src_network=src_net,
        dst_network=dst_net,
        src_wildcard=src_wildcard,
        src_ip_int=src_ip_int,
        dst_wildcard=dst_wildcard,
        dst_ip_int=dst_ip_int,
        dst_port_op=port_op,
        dst_port=port_val,
        raw_text=line.strip(),
    )


def parse_device_config(name: str, config_text: str) -> DeviceConfigModel:
    """Parse Cisco IOS configuration into structured model for evaluation."""
    dev = DeviceConfigModel(name=name)
    lines = config_text.splitlines()

    current_intf: ParsedInterface | None = None
    current_acl_name: str | None = None
    acl_line_counter = 0

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("!"):
            current_intf = None
            current_acl_name = None
            continue

        # Interface block properties
        if current_intf is not None:
            if line.startswith("description "):
                current_intf.description = line[len("description "):]
            elif line.startswith("ip vrf forwarding "):
                current_intf.vrf = line.split()[3]
            elif line.startswith("ip address "):
                parts = line.split()
                if len(parts) >= 4:
                    ip_addr = ipaddress.IPv4Address(parts[2])
                    net = ipaddress.IPv4Network(f"{parts[2]}/{parts[3]}", strict=False)
                    current_intf.ip_address = ip_addr
                    current_intf.network = net
            elif line.startswith("ip access-group "):
                parts = line.split()
                if len(parts) >= 4:
                    acl_name = parts[2]
                    direction = parts[3].lower()
                    if direction == "in":
                        current_intf.acl_in = acl_name
                    elif direction == "out":
                        current_intf.acl_out = acl_name
            continue

        # Hostname
        if line.startswith("hostname "):
            dev.name = line.split()[1]
            continue

        # VRF definition
        if line.startswith("ip vrf ") and not line.startswith("ip vrf forwarding "):
            vrf_name = line.split()[2]
            dev.vrfs.add(vrf_name)
            continue

        # Interface block start
        if line.startswith("interface "):
            intf_name = line.split()[1]
            current_intf = ParsedInterface(name=intf_name)
            dev.interfaces[intf_name] = current_intf
            current_acl_name = None
            continue

        # ACL block
        if line.startswith("ip access-list extended "):
            current_acl_name = line.split()[3]
            dev.acls.setdefault(current_acl_name, [])
            acl_line_counter = 0
            continue

        if current_acl_name is not None:
            acl_line_counter += 1
            rule = _parse_acl_line(line, acl_line_counter)
            if rule is not None:
                dev.acls[current_acl_name].append(rule)
            continue

        # Static route
        if line.startswith("ip route "):
            parts = line.split()
            # ip route [vrf <vrf>] <prefix> <mask/len> <next_hop>
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
                net = ipaddress.IPv4Network(f"{pfx}/{msk}", strict=False)
                nh_ip = ipaddress.IPv4Address(nh)
                dev.static_routes.append(ParsedStaticRoute(vrf=vrf, network=net, next_hop=nh_ip))
            except Exception:
                pass

    return dev


class ReferenceOracle(Oracle):
    """Pure-Python reference oracle evaluating routing and ACL policies."""

    def __init__(self) -> None:
        pass

    def _build_models(self, configs: dict[str, str]) -> dict[str, DeviceConfigModel]:
        return {name: parse_device_config(name, cfg) for name, cfg in configs.items()}

    def _resolve_ip(self, ip_or_cidr: str) -> ipaddress.IPv4Address:
        clean = ip_or_cidr.strip()
        if "/" in clean:
            net = ipaddress.IPv4Network(clean, strict=False)
            # Pick a representative host (host 10 if available, else first host)
            hosts = list(net.hosts())
            if len(hosts) >= 10:
                return hosts[9]
            elif hosts:
                return hosts[0]
            return net.network_address
        return ipaddress.IPv4Address(clean)

    def _simulate_packet(
        self,
        dev_models: dict[str, DeviceConfigModel],
        src_ip: ipaddress.IPv4Address,
        dst_ip: ipaddress.IPv4Address,
        proto: str,
        dst_port: int | None,
        req_via: str | None,
    ) -> FailureDetail:
        """Simulate packet forwarding and ACL evaluation across the network."""
        # 1. Find ingress router and interface
        ingress_dev: DeviceConfigModel | None = None
        ingress_intf: ParsedInterface | None = None

        for dev in dev_models.values():
            for intf in dev.interfaces.values():
                if intf.network is not None and src_ip in intf.network:
                    ingress_dev = dev
                    ingress_intf = intf
                    break
            if ingress_dev is not None:
                break

        if ingress_dev is None or ingress_intf is None:
            return FailureDetail(
                invariant_id="",
                passed=False,
                reason=f"Source IP {src_ip} not found in any device interface subnet.",
            )

        # 2. Find target destination device and interface
        dest_dev: DeviceConfigModel | None = None
        dest_intf: ParsedInterface | None = None

        for dev in dev_models.values():
            for intf in dev.interfaces.values():
                if intf.network is not None and dst_ip in intf.network:
                    dest_dev = dev
                    dest_intf = intf
                    break
            if dest_dev is not None:
                break

        if dest_dev is None or dest_intf is None:
            return FailureDetail(
                invariant_id="",
                passed=False,
                reason=f"Destination IP {dst_ip} not found in any device interface subnet.",
            )

        # 3. Simulate hop-by-hop traversal
        curr_dev = ingress_dev
        curr_vrf = ingress_intf.vrf
        visited: list[str] = [curr_dev.name]
        max_hops = 64

        # Check ingress interface ACL
        if ingress_intf.acl_in and ingress_intf.acl_in in curr_dev.acls:
            acl_rules = curr_dev.acls[ingress_intf.acl_in]
            matched = False
            for rule in acl_rules:
                if rule.matches(proto, src_ip, dst_ip, dst_port):
                    matched = True
                    if rule.action == "deny":
                        return FailureDetail(
                            invariant_id="",
                            passed=False,
                            blocking_device=curr_dev.name,
                            blocking_acl=ingress_intf.acl_in,
                            blocking_line=rule.line_no,
                            rule_text=rule.raw_text,
                            reason=f"Inbound ACL '{ingress_intf.acl_in}' on {curr_dev.name}:{ingress_intf.name} line {rule.line_no} denied traffic.",
                        )
                    break
            if not matched:
                return FailureDetail(
                    invariant_id="",
                    passed=False,
                    blocking_device=curr_dev.name,
                    blocking_acl=ingress_intf.acl_in,
                    reason=f"Implicit deny on {curr_dev.name}:{ingress_intf.name} ACL '{ingress_intf.acl_in}'.",
                )

        # Hop loop
        for _ in range(max_hops):
            # Check if destination reached directly on curr_dev
            if curr_dev.name == dest_dev.name:
                if curr_vrf != dest_intf.vrf:
                    return FailureDetail(
                        invariant_id="",
                        passed=False,
                        blocking_device=curr_dev.name,
                        reason=f"VRF mismatch at destination device {curr_dev.name}: packet in VRF '{curr_vrf}', destination in VRF '{dest_intf.vrf}'.",
                    )
                # Reached!
                if req_via is not None and req_via not in visited:
                    return FailureDetail(
                        invariant_id="",
                        passed=False,
                        reason=f"Packet delivered but failed to traverse required waypoint '{req_via}'. Visited path: {' -> '.join(visited)}.",
                    )
                return FailureDetail(
                    invariant_id="",
                    passed=True,
                    reason=f"Packet successfully delivered to destination. Path: {' -> '.join(visited)}.",
                )

            # Longest prefix match static route lookup
            best_route: ParsedStaticRoute | None = None
            best_prefixlen = -1

            for route in curr_dev.static_routes:
                if route.vrf == curr_vrf and dst_ip in route.network:
                    if route.network.prefixlen > best_prefixlen:
                        best_prefixlen = route.network.prefixlen
                        best_route = route

            if best_route is None:
                return FailureDetail(
                    invariant_id="",
                    passed=False,
                    blocking_device=curr_dev.name,
                    reason=f"No route to destination {dst_ip} at device '{curr_dev.name}' in VRF '{curr_vrf}'.",
                )

            next_hop = best_route.next_hop

            # Find which egress interface on curr_dev connects to next_hop
            egress_intf: ParsedInterface | None = None
            next_dev: DeviceConfigModel | None = None

            for intf in curr_dev.interfaces.values():
                if intf.network is not None and next_hop in intf.network:
                    egress_intf = intf
                    break

            if egress_intf is None:
                return FailureDetail(
                    invariant_id="",
                    passed=False,
                    blocking_device=curr_dev.name,
                    reason=f"Next hop {next_hop} at '{curr_dev.name}' is not directly connected.",
                )

            # Check egress interface ACL on curr_dev
            if egress_intf.acl_out and egress_intf.acl_out in curr_dev.acls:
                matched_out = False
                for rule in curr_dev.acls[egress_intf.acl_out]:
                    if rule.matches(proto, src_ip, dst_ip, dst_port):
                        matched_out = True
                        if rule.action == "deny":
                            return FailureDetail(
                                invariant_id="",
                                passed=False,
                                blocking_device=curr_dev.name,
                                blocking_acl=egress_intf.acl_out,
                                blocking_line=rule.line_no,
                                rule_text=rule.raw_text,
                                reason=f"Outbound ACL '{egress_intf.acl_out}' on {curr_dev.name}:{egress_intf.name} line {rule.line_no} denied traffic.",
                            )
                        break
                if not matched_out:
                    return FailureDetail(
                        invariant_id="",
                        passed=False,
                        blocking_device=curr_dev.name,
                        blocking_acl=egress_intf.acl_out,
                        reason=f"Implicit deny on outbound ACL '{egress_intf.acl_out}' on {curr_dev.name}:{egress_intf.name}.",
                    )

            # Find next hop device
            next_intf: ParsedInterface | None = None
            for dev in dev_models.values():
                if dev.name == curr_dev.name:
                    continue
                for intf in dev.interfaces.values():
                    if intf.ip_address == next_hop:
                        next_dev = dev
                        next_intf = intf
                        # Ingress ACL check on neighbor ingress interface
                        if intf.acl_in and intf.acl_in in dev.acls:
                            matched_in = False
                            for rule in dev.acls[intf.acl_in]:
                                if rule.matches(proto, src_ip, dst_ip, dst_port):
                                    matched_in = True
                                    if rule.action == "deny":
                                        return FailureDetail(
                                            invariant_id="",
                                            passed=False,
                                            blocking_device=dev.name,
                                            blocking_acl=intf.acl_in,
                                            blocking_line=rule.line_no,
                                            rule_text=rule.raw_text,
                                            reason=f"Inbound ACL '{intf.acl_in}' on {dev.name}:{intf.name} line {rule.line_no} denied traffic.",
                                        )
                                    break
                            if not matched_in:
                                return FailureDetail(
                                    invariant_id="",
                                    passed=False,
                                    blocking_device=dev.name,
                                    blocking_acl=intf.acl_in,
                                    reason=f"Implicit deny on inbound ACL '{intf.acl_in}' on {dev.name}:{intf.name}.",
                                )
                        break
                if next_dev is not None:
                    break

            if next_dev is None or next_intf is None:
                return FailureDetail(
                    invariant_id="",
                    passed=False,
                    blocking_device=curr_dev.name,
                    reason=f"Next hop IP {next_hop} does not resolve to any active device interface.",
                )

            curr_dev = next_dev
            curr_vrf = next_intf.vrf
            visited.append(curr_dev.name)

        return FailureDetail(
            invariant_id="",
            passed=False,
            reason=f"Routing loop or maximum hop limit exceeded ({max_hops}). Visited path: {' -> '.join(visited)}.",
        )

    def check(self, configs: dict[str, str], invariants: list[Invariant]) -> dict[str, bool]:
        """Check all invariants against device configurations."""
        dev_models = self._build_models(configs)
        results: dict[str, bool] = {}

        for inv in invariants:
            src_ip = self._resolve_ip(inv.source)
            dst_ip = self._resolve_ip(inv.destination)
            proto = str(inv.headers.get("protocol", "tcp")).lower()
            dst_port = inv.headers.get("dst_port")

            sim_res = self._simulate_packet(
                dev_models=dev_models,
                src_ip=src_ip,
                dst_ip=dst_ip,
                proto=proto,
                dst_port=dst_port,
                req_via=(inv.via or inv.headers.get("waypoint")) if inv.kind == InvariantKind.WAYPOINT else None,
            )

            if inv.kind == InvariantKind.REACH:
                results[inv.id] = sim_res.passed
            elif inv.kind == InvariantKind.ISOLATE:
                # Isolate expects packet to NOT be delivered
                results[inv.id] = not sim_res.passed
            elif inv.kind == InvariantKind.WAYPOINT:
                results[inv.id] = sim_res.passed

        return results

    def explain_failure(self, configs: dict[str, str], invariant: Invariant) -> FailureDetail:
        """Provide detailed root cause explanation for why an invariant failed."""
        dev_models = self._build_models(configs)
        src_ip = self._resolve_ip(invariant.source)
        dst_ip = self._resolve_ip(invariant.destination)
        proto = str(invariant.headers.get("protocol", "tcp")).lower()
        dst_port = invariant.headers.get("dst_port")

        sim_res = self._simulate_packet(
            dev_models=dev_models,
            src_ip=src_ip,
            dst_ip=dst_ip,
            proto=proto,
            dst_port=dst_port,
            req_via=(invariant.via or invariant.headers.get("waypoint")) if invariant.kind == InvariantKind.WAYPOINT else None,
        )

        if invariant.kind == InvariantKind.REACH:
            passed = sim_res.passed
            reason = sim_res.reason
        elif invariant.kind == InvariantKind.ISOLATE:
            passed = not sim_res.passed
            reason = (
                "Traffic was delivered successfully, violating isolation policy."
                if sim_res.passed
                else sim_res.reason
            )
        else:  # WAYPOINT
            passed = sim_res.passed
            reason = sim_res.reason

        return FailureDetail(
            invariant_id=invariant.id,
            passed=passed,
            blocking_device=sim_res.blocking_device,
            blocking_acl=sim_res.blocking_acl,
            blocking_line=sim_res.blocking_line,
            rule_text=sim_res.rule_text,
            reason=reason,
        )
