"""Deterministic Cisco IOS configuration generator from topology specifications."""

from collections import deque
import ipaddress
from generator.topology.spec import NodeRole, TopologySpec


def _compute_next_hops(spec: TopologySpec) -> dict[str | None, dict[str, dict[str, str]]]:
    """Compute shortest-path next hop IP per VRF (None for global) from every node to every other node.

    Returns:
        Mapping: vrf -> node -> destination_node -> next_hop_ip_on_link
    """
    vrf_keys: list[str | None] = [None] + list(spec.vrfs)
    next_hops: dict[str | None, dict[str, dict[str, str]]] = {}

    for vrf_key in vrf_keys:
        adj: dict[str, list[tuple[str, str, str]]] = {name: [] for name in spec.nodes}
        for link in spec.links:
            if link.vrf == vrf_key:
                adj[link.node1].append((link.node2, link.intf1, link.ip2))
                adj[link.node2].append((link.node1, link.intf2, link.ip1))

        nh_for_vrf: dict[str, dict[str, str]] = {name: {} for name in spec.nodes}
        for src in sorted(spec.nodes.keys()):
            queue = deque([src])
            visited = {src}
            parent_first_hop: dict[str, str] = {}

            while queue:
                curr = queue.popleft()
                for neighbor, local_intf, neighbor_ip in sorted(adj[curr], key=lambda x: x[0]):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        if curr == src:
                            parent_first_hop[neighbor] = neighbor_ip
                        else:
                            parent_first_hop[neighbor] = parent_first_hop[curr]
                        queue.append(neighbor)

            nh_for_vrf[src] = parent_first_hop
        next_hops[vrf_key] = nh_for_vrf

    return next_hops


def generate_device_config(spec: TopologySpec, node_name: str, next_hops: dict[str, dict[str, str]]) -> str:
    """Generate a clean, deterministic Cisco IOS configuration for a single device."""
    node = spec.nodes[node_name]
    lines: list[str] = [f"hostname {node.name}", "!"]

    # 1. VRF Declarations
    if node.vrfs:
        for idx, vrf in enumerate(sorted(node.vrfs), start=1):
            lines.append(f"ip vrf {vrf}")
            lines.append(f" rd 65000:{idx * 10}")
            lines.append("!")

    # 2. Point-to-point link interfaces
    # Find all links incident to this node
    node_links = []
    for link in spec.links:
        if link.node1 == node_name:
            node_links.append((link.intf1, f"Link to {link.node2}", link.ip1, link.netmask, link.vrf))
        elif link.node2 == node_name:
            node_links.append((link.intf2, f"Link to {link.node1}", link.ip2, link.netmask, link.vrf))

    for intf_name, desc, ip, mask, vrf in sorted(node_links, key=lambda x: x[0]):
        lines.append(f"interface {intf_name}")
        lines.append(f" description {desc}")
        if vrf:
            lines.append(f" ip vrf forwarding {vrf}")
        lines.append(f" ip address {ip} {mask}")
        if node.role in (NodeRole.EDGE, NodeRole.FIREWALL):
            lines.append(" ip access-group EDGE_IN in")
        lines.append("!")

    # 3. Host LAN interfaces
    for hsub in sorted(node.host_subnets, key=lambda s: s.interface):
        lines.append(f"interface {hsub.interface}")
        lines.append(f" description Host LAN {hsub.network_cidr}")
        if hsub.vrf:
            lines.append(f" ip vrf forwarding {hsub.vrf}")
        lines.append(f" ip address {hsub.gateway_ip} {hsub.netmask}")
        if node.role in (NodeRole.EDGE, NodeRole.FIREWALL):
            lines.append(" ip access-group EDGE_IN in")
        lines.append("!")

    # 4. Baseline Extended ACLs
    # 4. Baseline Extended ACLs
    if node.role == NodeRole.FIREWALL:
        lines.append("ip access-list extended EDGE_IN")
        lines.append(" deny tcp any any eq 22")
        lines.append(" deny tcp any any eq 23")
        lines.append(" deny tcp any any eq 3389")
        lines.append(" permit icmp any any")
        lines.append(" permit tcp any any eq 53")
        lines.append(" permit udp any any eq 53")
        lines.append(" permit tcp any any eq 80")
        lines.append(" permit tcp any any eq 443")
        lines.append(" permit ip any any")
        lines.append("!")
    elif node.role == NodeRole.EDGE:
        lines.append("ip access-list extended EDGE_IN")
        lines.append(" permit icmp any any")
        lines.append(" permit tcp any any eq 53")
        lines.append(" permit udp any any eq 53")
        lines.append(" permit tcp any any eq 80")
        lines.append(" permit tcp any any eq 443")
        lines.append(" permit ip any any")
        lines.append("!")

    # 5. Baseline Prefix-Lists and Route-Maps
    lines.append("ip prefix-list PL_DEFAULT seq 10 permit 0.0.0.0/0 le 32")
    lines.append("!")
    lines.append("route-map RM_DEFAULT permit 10")
    lines.append(" match ip address prefix-list PL_DEFAULT")
    lines.append("!")

    # 6. Static Routing to all other host subnets
    # For every other node with host subnets, route via next hop per VRF
    static_routes: list[str] = []

    for target_name, target_node in sorted(spec.nodes.items()):
        if target_name == node_name:
            continue

        for hsub in target_node.host_subnets:
            nh_map = next_hops.get(hsub.vrf, {}).get(node_name, {})
            next_hop_ip = nh_map.get(target_name)
            if not next_hop_ip:
                next_hop_ip = next_hops.get(None, {}).get(node_name, {}).get(target_name)
            if not next_hop_ip:
                continue

            net = ipaddress.ip_network(hsub.network_cidr)
            net_addr = str(net.network_address)
            net_mask = str(net.netmask)
            if hsub.vrf:
                # VRF static route
                static_routes.append(f"ip route vrf {hsub.vrf} {net_addr} {net_mask} {next_hop_ip}")
            else:
                static_routes.append(f"ip route {net_addr} {net_mask} {next_hop_ip}")

    for r in sorted(static_routes):
        lines.append(r)

    lines.append("!")
    return "\n".join(lines).strip() + "\n"


def generate_topology_configs(spec: TopologySpec) -> dict[str, str]:
    """Generate configs for all devices in a topology specification."""
    next_hops = _compute_next_hops(spec)
    configs: dict[str, str] = {}
    for node_name in spec.inventory:
        configs[node_name] = generate_device_config(spec, node_name, next_hops)
    return configs
