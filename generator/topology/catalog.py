"""Catalog of standard synthetic topologies for train and test splits."""

import ipaddress
from agent.contract.models import Invariant, InvariantKind
from generator.topology.generator import generate_topology_configs
from generator.topology.spec import HostSubnet, Link, NodeRole, TopologyNode, TopologySpec


def _build_topology_spec(
    topo_id: str,
    split: str,
    node_prefix: str,
    num_nodes: int,
    link_base_ip: str,
    host_base_ip: str,
    vrf_names: list[str],
) -> TopologySpec:
    """Deterministically construct a synthetic topology with graph, subnets, and baseline invariants."""
    nodes: dict[str, TopologyNode] = {}
    links: list[Link] = []

    # Parse base address octets
    link_parts = [int(p) for p in link_base_ip.split(".")]
    host_parts = [int(p) for p in host_base_ip.split(".")]

    # Designate fw1 as waypoint transit node with management host subnet
    fw_name = f"{node_prefix}fw1"
    fw_hsubs = [
        HostSubnet(
            interface="GigabitEthernet0/10",
            network_cidr=f"{host_parts[0]}.{host_parts[1]}.0.0/24",
            gateway_ip=f"{host_parts[0]}.{host_parts[1]}.0.1",
            netmask="255.255.255.0",
            vrf=None,
        )
    ]
    fw_node = TopologyNode(name=fw_name, role=NodeRole.FIREWALL, host_subnets=fw_hsubs, vrfs=vrf_names)
    nodes[fw_name] = fw_node

    router_names = [f"{node_prefix}r{i}" for i in range(1, num_nodes)]
    # Half left, half right of firewall to establish transit path through firewall
    half = (num_nodes - 1) // 2
    left_routers = router_names[:half]
    right_routers = router_names[half:]

    # Create router nodes
    for idx, r_name in enumerate(router_names, start=1):
        role = NodeRole.EDGE if (idx in (1, 2, num_nodes - 2, num_nodes - 1)) else NodeRole.CORE
        # Assign host subnets to edge routers
        hsubs: list[HostSubnet] = []
        vrf_assignments: list[str] = []

        if role == NodeRole.EDGE:
            # Global host subnet
            net_str = f"{host_parts[0]}.{host_parts[1]}.{idx}.0/24"
            gw_str = f"{host_parts[0]}.{host_parts[1]}.{idx}.1"
            hsubs.append(HostSubnet(
                interface="GigabitEthernet0/10",
                network_cidr=net_str,
                gateway_ip=gw_str,
                netmask="255.255.255.0",
                vrf=None,
            ))
            # VRF host subnets
            vrf_assignments = list(vrf_names)
            for v_idx, vrf in enumerate(vrf_names, start=1):
                vrf_net_str = f"{host_parts[0]}.{host_parts[1]}.{100 * v_idx + idx}.0/24"
                vrf_gw_str = f"{host_parts[0]}.{host_parts[1]}.{100 * v_idx + idx}.1"
                hsubs.append(HostSubnet(
                    interface=f"GigabitEthernet0/{10 + v_idx}",
                    network_cidr=vrf_net_str,
                    gateway_ip=vrf_gw_str,
                    netmask="255.255.255.0",
                    vrf=vrf,
                ))

        nodes[r_name] = TopologyNode(
            name=r_name,
            role=role,
            host_subnets=hsubs,
            vrfs=vrf_assignments,
        )

    link_idx = 0

    def make_link(n1: str, n2: str, vrf: str | None = None) -> Link:
        nonlocal link_idx
        link_idx += 1
        oct3 = (link_idx * 4) // 256
        oct4 = (link_idx * 4) % 256
        net_str = f"{link_parts[0]}.{link_parts[1]}.{oct3}.{oct4}/30"
        ip1 = f"{link_parts[0]}.{link_parts[1]}.{oct3}.{oct4 + 1}"
        ip2 = f"{link_parts[0]}.{link_parts[1]}.{oct3}.{oct4 + 2}"
        intf1 = f"GigabitEthernet0/{len([l for l in links if l.node1 == n1 or l.node2 == n1])}"
        intf2 = f"GigabitEthernet0/{len([l for l in links if l.node1 == n2 or l.node2 == n2])}"
        return Link(node1=n1, node2=n2, intf1=intf1, intf2=intf2, subnet_cidr=net_str, ip1=ip1, ip2=ip2, vrf=vrf)

    # Hierarchical hub structure:
    # Left hub connects to fw_name, right hub connects to fw_name
    if left_routers:
        left_hub = left_routers[-1]
        links.append(make_link(left_hub, fw_name))
        # Connect remaining left routers to left_hub in small chains of at most 2
        for i in range(len(left_routers) - 1):
            if i % 2 == 0:
                links.append(make_link(left_routers[i], left_hub))
            else:
                links.append(make_link(left_routers[i], left_routers[i - 1]))

    if right_routers:
        right_hub = right_routers[0]
        links.append(make_link(fw_name, right_hub))
        # Connect remaining right routers to right_hub in small chains of at most 2
        for i in range(1, len(right_routers)):
            if (i - 1) % 2 == 0:
                links.append(make_link(right_routers[i], right_hub))
            else:
                links.append(make_link(right_routers[i], right_routers[i - 1]))

    # Add intra-VRF link between adjacent edge/core routers
    if len(router_names) >= 2 and vrf_names:
        for v in vrf_names:
            links.append(make_link(router_names[0], router_names[1], vrf=v))

    # Generate 20 baseline invariants
    # Identify edge host subnets on left and right
    r_left_edge = left_routers[0] if left_routers else fw_name
    r_right_edge = right_routers[-1] if right_routers else fw_name
    r_left_hsubs = nodes[r_left_edge].host_subnets
    r_right_hsubs = nodes[r_right_edge].host_subnets

    h_left_global = next((s for s in r_left_hsubs if s.vrf is None), None)
    h_right_global = next((s for s in r_right_hsubs if s.vrf is None), None)

    # Subnet IPs for testing
    src_ip = h_left_global.gateway_ip[:-1] + "10" if h_left_global else f"{host_parts[0]}.{host_parts[1]}.1.10"
    dst_ip = h_right_global.gateway_ip[:-1] + "10" if h_right_global else f"{host_parts[0]}.{host_parts[1]}.{num_nodes-1}.10"

    invariants: list[Invariant] = []

    # 1-7: REACH invariants (standard permitted ports between global endpoints)
    standard_ports = [80, 443, 53]
    inv_num = 1
    for port in standard_ports:
        # Use wildcard edge IP ending in .0 for first invariant to be sensitive to inverted wildcard mask errors
        s_ip = f"{host_parts[0]}.{host_parts[1]}.1.0" if inv_num == 1 else src_ip
        invariants.append(Invariant(
            id=f"{topo_id}_inv_{inv_num:02d}_reach_tcp_{port}",
            kind=InvariantKind.REACH,
            source=s_ip,
            destination=dst_ip,
            headers={"protocol": "tcp", "dst_port": port},
            created_at_step=0,
        ))
        inv_num += 1

    invariants.append(Invariant(
        id=f"{topo_id}_inv_{inv_num:02d}_reach_icmp",
        kind=InvariantKind.REACH,
        source=src_ip,
        destination=dst_ip,
        headers={"protocol": "icmp"},
        created_at_step=0,
    ))
    inv_num += 1

    invariants.append(Invariant(
        id=f"{topo_id}_inv_{inv_num:02d}_reach_reverse_tcp_80",
        kind=InvariantKind.REACH,
        source=dst_ip,
        destination=src_ip,
        headers={"protocol": "tcp", "dst_port": 80},
        created_at_step=0,
    ))
    inv_num += 1

    invariants.append(Invariant(
        id=f"{topo_id}_inv_{inv_num:02d}_reach_reverse_tcp_443",
        kind=InvariantKind.REACH,
        source=dst_ip,
        destination=src_ip,
        headers={"protocol": "tcp", "dst_port": 443},
        created_at_step=0,
    ))
    inv_num += 1

    invariants.append(Invariant(
        id=f"{topo_id}_inv_{inv_num:02d}_reach_udp_53",
        kind=InvariantKind.REACH,
        source=src_ip,
        destination=dst_ip,
        headers={"protocol": "udp", "dst_port": 53},
        created_at_step=0,
    ))
    inv_num += 1

    # 8-14: ISOLATE invariants (denied ports 22, 23, 3389 and cross-VRF isolation)
    denied_ports = [22, 23, 3389]
    for port in denied_ports:
        invariants.append(Invariant(
            id=f"{topo_id}_inv_{inv_num:02d}_isolate_tcp_{port}",
            kind=InvariantKind.ISOLATE,
            source=src_ip,
            destination=dst_ip,
            headers={"protocol": "tcp", "dst_port": port},
            created_at_step=0,
        ))
        inv_num += 1
        invariants.append(Invariant(
            id=f"{topo_id}_inv_{inv_num:02d}_isolate_reverse_tcp_{port}",
            kind=InvariantKind.ISOLATE,
            source=dst_ip,
            destination=src_ip,
            headers={"protocol": "tcp", "dst_port": port},
            created_at_step=0,
        ))
        inv_num += 1

    # VRF cross-isolation
    vrf1_src = f"{host_parts[0]}.{host_parts[1]}.101.10"
    vrf2_dst = f"{host_parts[0]}.{host_parts[1]}.201.10"
    invariants.append(Invariant(
        id=f"{topo_id}_inv_{inv_num:02d}_isolate_vrf_red_to_blue",
        kind=InvariantKind.ISOLATE,
        source=vrf1_src,
        destination=vrf2_dst,
        headers={"protocol": "tcp", "dst_port": 80},
        created_at_step=0,
    ))
    inv_num += 1

    # Intra-VRF reachability across adjacent routers (sensitive to VRF static route omission)
    if vrf_names and len(router_names) >= 2:
        invariants.append(Invariant(
            id=f"{topo_id}_inv_{inv_num:02d}_reach_intra_vrf_{vrf_names[0].lower()}",
            kind=InvariantKind.REACH,
            source=f"{host_parts[0]}.{host_parts[1]}.101.10",
            destination=f"{host_parts[0]}.{host_parts[1]}.102.10",
            headers={"protocol": "tcp", "dst_port": 80},
            created_at_step=0,
        ))
        inv_num += 1

    # Firewall management isolation (sensitive to inbound vs outbound ACL direction)
    invariants.append(Invariant(
        id=f"{topo_id}_inv_{inv_num:02d}_isolate_fw_mgmt_ssh",
        kind=InvariantKind.ISOLATE,
        source=src_ip,
        destination=f"{host_parts[0]}.{host_parts[1]}.0.10",
        headers={"protocol": "tcp", "dst_port": 22},
        created_at_step=0,
    ))
    inv_num += 1

    # 15-20: WAYPOINT invariants (traffic traversing firewall node)
    waypoint_specs = [
        ("tcp", 80),
        ("tcp", 443),
        ("udp", 53),
        ("icmp", None),
    ]
    for proto, port in waypoint_specs:
        hdr = {"protocol": proto}
        if port is not None:
            hdr["dst_port"] = port
        invariants.append(Invariant(
            id=f"{topo_id}_inv_{inv_num:02d}_waypoint_{proto}_{port or 'none'}",
            kind=InvariantKind.WAYPOINT,
            source=src_ip,
            destination=dst_ip,
            headers=hdr,
            via=fw_name,
            created_at_step=0,
        ))
        inv_num += 1

    # Extra reverse waypoint
    invariants.append(Invariant(
        id=f"{topo_id}_inv_{inv_num:02d}_waypoint_reverse_tcp_80",
        kind=InvariantKind.WAYPOINT,
        source=dst_ip,
        destination=src_ip,
        headers={"protocol": "tcp", "dst_port": 80},
        via=fw_name,
        created_at_step=0,
    ))
    inv_num += 1

    invariants.append(Invariant(
        id=f"{topo_id}_inv_{inv_num:02d}_waypoint_reverse_tcp_443",
        kind=InvariantKind.WAYPOINT,
        source=dst_ip,
        destination=src_ip,
        headers={"protocol": "tcp", "dst_port": 443},
        via=fw_name,
        created_at_step=0,
    ))

    return TopologySpec(
        id=topo_id,
        split=split,  # type: ignore[arg-type]
        nodes=nodes,
        links=links,
        firewall_node=fw_name,
        vrfs=vrf_names,
        baseline_invariants=invariants,
    )


# Catalog definitions
TOPOLOGIES: dict[str, TopologySpec] = {
    # Train Topologies
    "topo_train_small": _build_topology_spec(
        topo_id="topo_train_small",
        split="train",
        node_prefix="tr_",
        num_nodes=10,
        link_base_ip="10.10.0.0",
        host_base_ip="10.11.0.0",
        vrf_names=["RED", "BLUE"],
    ),
    "topo_train_medium": _build_topology_spec(
        topo_id="topo_train_medium",
        split="train",
        node_prefix="tr_m_",
        num_nodes=25,
        link_base_ip="10.30.0.0",
        host_base_ip="10.31.0.0",
        vrf_names=["RED", "BLUE"],
    ),
    # Test Topologies
    "topo_test_small": _build_topology_spec(
        topo_id="topo_test_small",
        split="test",
        node_prefix="te_s_",
        num_nodes=6,
        link_base_ip="10.20.0.0",
        host_base_ip="10.21.0.0",
        vrf_names=["CORP", "GUEST"],
    ),
    "topo_test_large": _build_topology_spec(
        topo_id="topo_test_large",
        split="test",
        node_prefix="te_l_",
        num_nodes=50,
        link_base_ip="10.50.0.0",
        host_base_ip="10.51.0.0",
        vrf_names=["CORP", "GUEST"],
    ),
}


def get_topology(topo_id: str) -> TopologySpec:
    """Retrieve topology specification by identifier."""
    if topo_id not in TOPOLOGIES:
        raise KeyError(f"Topology '{topo_id}' not found. Available: {list(TOPOLOGIES.keys())}")
    return TOPOLOGIES[topo_id]


def list_topologies(split: str | None = None) -> list[TopologySpec]:
    """List topology specifications, optionally filtered by train or test split."""
    if split is None:
        return list(TOPOLOGIES.values())
    return [t for t in TOPOLOGIES.values() if t.split == split]
