"""Audit Debug: Inspect step 10 of topo_test_large_test_seq_001."""

import json
from audit.independent_checker import IndependentAuditChecker
from generator.oracle.reference import ReferenceOracle
from generator.topology.catalog import get_topology
from generator.topology.generator import generate_topology_configs
from generator.sequences.builder import build_sequence
from agent.contract.models import Intent

def main():
    spec = get_topology("topo_test_large")
    seq_gen = build_sequence(spec=spec, length=50, seed=1, sequence_id="topo_test_large_test_seq_001", split="test")

    intents = seq_gen.intents
    baseline_count = len(spec.baseline_invariants)

    configs = generate_topology_configs(spec)
    for i in range(11):
        configs.update(intents[i].gold_patch)

    inv_10 = seq_gen.invariants[baseline_count + 10]
    print(f"Invariant at step 10: {inv_10}")

    ref_oracle = ReferenceOracle()
    audit_oracle = IndependentAuditChecker()

    ref_res = ref_oracle.check(configs, [inv_10])
    audit_res = audit_oracle.check(configs, [inv_10])

    print(f"ReferenceOracle check: {ref_res}")
    print(f"IndependentAuditChecker check: {audit_res}")

    # Explain failure from ReferenceOracle
    fail_detail = ref_oracle.explain_failure(configs, inv_10)
    print(f"ReferenceOracle explain_failure: passed={fail_detail.passed}, reason={fail_detail.reason}, dev={fail_detail.blocking_device}, acl={fail_detail.blocking_acl}")

    # Let's inspect the path in IndependentAuditChecker
    audit_nodes = {name: audit_oracle._evaluate_invariant.__globals__["parse_cisco_config"](name, cfg) for name, cfg in configs.items()}
    # Let's trace packet manually
    src_ip = inv_10.source
    dst_ip = inv_10.destination
    print(f"src: {src_ip}, dst: {dst_ip}, headers: {inv_10.headers}")

    # Check which device has src and dst
    for name, node in audit_nodes.items():
        for intf_name, intf in node.interfaces.items():
            if intf.network and intf.ip:
                import ipaddress
                if ipaddress.IPv4Address(src_ip) in intf.network:
                    print(f"Src on {name}:{intf_name} (ip={intf.ip}, vrf={intf.vrf}, acl_in={intf.acl_in})")
                if ipaddress.IPv4Address(dst_ip) in intf.network:
                    print(f"Dst on {name}:{intf_name} (ip={intf.ip}, vrf={intf.vrf}, acl_in={intf.acl_in})")

if __name__ == "__main__":
    main()
