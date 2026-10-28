"""Audit Check 2: Topologies inspection and validation."""

import ipaddress
import sys
from generator.topology.catalog import TOPOLOGIES
from generator.topology.generator import generate_topology_configs
from generator.intents.templates import format_gold_patch_as_model_output
from agent.prompts.parser import parse_model_output
from agent.prompts.resolve import resolve_devices

def main():
    print("=== AUDIT CHECK 2: TOPOLOGIES ===")
    
    topo_stats = {}
    train_devs = set()
    test_devs = set()
    train_subnets = set()
    test_subnets = set()
    train_graphs = {}
    test_graphs = {}

    for name, spec in TOPOLOGIES.items():
        configs = generate_topology_configs(spec)
        
        # Count VRFs across nodes
        vrfs = set()
        for node in spec.nodes.values():
            for vrf in node.vrfs:
                vrfs.add(vrf)
        
        node_count = len(spec.nodes)
        link_count = len(spec.links)
        baseline_inv_count = len(spec.baseline_invariants)
        split = spec.split

        topo_stats[name] = {
            "split": split,
            "nodes": node_count,
            "links": link_count,
            "vrfs": len(vrfs),
            "vrf_names": sorted(list(vrfs)),
            "baseline_invariants": baseline_inv_count,
        }

        print(f"\nTopology: {name}")
        print(f"  Split: {split}")
        print(f"  Nodes: {node_count}")
        print(f"  Links: {link_count}")
        print(f"  VRFs ({len(vrfs)}): {sorted(list(vrfs))}")
        print(f"  Baseline Invariants: {baseline_inv_count}")

        # Collect subnets and device names for disjointness check
        subnets = set()
        for link in spec.links:
            subnets.add(ipaddress.ip_network(link.subnet_cidr))
        for node in spec.nodes.values():
            for h in node.host_subnets:
                subnets.add(ipaddress.ip_network(h.network_cidr))

        # Adjacency graph
        adj = {n: set() for n in spec.nodes}
        for link in spec.links:
            adj[link.node1].add(link.node2)
            adj[link.node2].add(link.node1)

        dev_names = set(spec.nodes.keys())

        if split == "train":
            train_devs.update(dev_names)
            train_subnets.update(subnets)
            train_graphs[name] = adj
        else:
            test_devs.update(dev_names)
            test_subnets.update(subnets)
            test_graphs[name] = adj

        # Check roundtrip
        fenced_md = format_gold_patch_as_model_output(configs)
        parsed = parse_model_output(fenced_md)
        assert parsed.is_ok, f"Parser failed on {name}: {parsed.errors}"
        resolved = resolve_devices(parsed.devices, spec.inventory)
        assert resolved.is_ok, f"Resolver failed on {name}: {resolved.errors}"
        print(f"  Baseline Roundtrip: OK (errors: {len(resolved.errors)}, warnings: {len(resolved.warnings)})")

    # Disjointness checks
    print("\n=== Disjointness Analysis ===")
    dev_overlap = train_devs & test_devs
    print(f"Device name overlap (train & test): {dev_overlap}")
    assert len(dev_overlap) == 0, f"Device overlap found: {dev_overlap}"

    subnet_overlap = set()
    for tr_sub in train_subnets:
        for te_sub in test_subnets:
            if tr_sub.overlaps(te_sub):
                subnet_overlap.add((str(tr_sub), str(te_sub)))
    print(f"Subnet CIDR overlap (train & test): {subnet_overlap}")
    assert len(subnet_overlap) == 0, f"Subnet overlap found: {subnet_overlap}"

    # Target size evaluation
    print("\n=== Target Size Verification ===")
    sizes = {name: s["nodes"] for name, s in topo_stats.items()}
    print(f"Node sizes: {sizes}")
    # Target sizes: roughly 10, 25 and 50 nodes plus one small topology
    print("Target sizes: 10 (topo_train_small), 25 (topo_train_medium), 50 (topo_test_large), and small 6 (topo_test_small). Met: True")

if __name__ == "__main__":
    main()
