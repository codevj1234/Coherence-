"""Audit Check 8: Corruption suite analysis and new corruption benchmark."""

import json
from pathlib import Path
import random
import sys

from audit.independent_checker import IndependentAuditChecker
from generator.oracle.reference import ReferenceOracle
from generator.topology.catalog import get_topology
from generator.topology.generator import generate_topology_configs
from agent.prompts.parser import parse_model_output
from agent.prompts.resolve import resolve_devices

def main():
    print("=== AUDIT CHECK 8: CORRUPTION SUITE QUALITY ===")

    # 1. Analyze existing 50 corruptions from replay_check.py
    c_types = ["malformed_fence", "delete_device", "drop_deny", "widen_prefix"]
    dist = {}
    for i in range(1, 51):
        ct = c_types[(i - 1) % len(c_types)]
        dist[ct] = dist.get(ct, 0) + 1

    print("\n--- Analysis of Existing 50-step Corruption Suite ---")
    print(f"Number of distinct corruption modes in generator.replay_check: {len(c_types)}")
    print(f"Distribution across 50 iterations: {dist}")
    print("Variety Assessment:")
    print("  The suite claims 5 modes in comments, but implements only 4 distinct synthetic mutations.")
    print("  Rather than injecting 50 varied nuanced network faults (e.g., ACL line position swaps,")
    print("  wildcard inverted masks, routing next-hop typos, VRF leakage), it repeats 4 high-level")
    print("  coarse checks 12-13 times each.")

    # 2. Implement 10 NEW nuanced corruptions
    print("\n--- Testing 10 NEW Realistic Network Policy Corruptions ---")
    new_corruptions = [
        "C01_acl_line_reorder_shadowing",
        "C02_wrong_wildcard_mask",
        "C03_wrong_route_next_hop",
        "C04_vrf_forwarding_swap",
        "C05_omitted_access_group_in",
        "C06_corrupted_interface_netmask",
        "C07_action_permit_deny_swap",
        "C08_static_route_vrf_omission",
        "C09_unrecognized_device_in_fence",
        "C10_direction_in_out_swap",
    ]

    spec = get_topology("topo_test_small")
    base_configs = generate_topology_configs(spec)
    oracle = ReferenceOracle()
    audit_oracle = IndependentAuditChecker()
    invs = spec.baseline_invariants

    detected_count = 0

    for c_id in new_corruptions:
        cfgs = dict(base_configs)
        detection_layer = None
        error_msg = ""

        if c_id == "C01_acl_line_reorder_shadowing":
            # Move permit ip any any to the very top of EDGE_IN on te_s_fw1
            cfg = cfgs["te_s_fw1"]
            cfgs["te_s_fw1"] = cfg.replace(
                "ip access-list extended EDGE_IN\n",
                "ip access-list extended EDGE_IN\n permit ip any any\n",
            ).replace(" permit ip any any\n!", "!")
            res = audit_oracle.check(cfgs, invs)
            if not all(res.values()):
                detection_layer = "Oracle (Broken Baseline Isolation)"
                error_msg = f"Failed invariants: {[k for k, v in res.items() if not v]}"

        elif c_id == "C02_wrong_wildcard_mask":
            # Insert rule with inverted wildcard mask 255.255.255.0 instead of 0.0.0.255
            cfg = cfgs["te_s_r1"]
            cfgs["te_s_r1"] = cfg.replace(
                "ip access-list extended EDGE_IN\n",
                "ip access-list extended EDGE_IN\n deny ip 10.21.1.0 255.255.255.0 any\n",
            )
            res = audit_oracle.check(cfgs, invs)
            if not all(res.values()):
                detection_layer = "Oracle (Traffic Disruption)"
                error_msg = f"Failed invariants: {[k for k, v in res.items() if not v]}"

        elif c_id == "C03_wrong_route_next_hop":
            # Change next hop of a route on te_s_r1 to unreachable IP 10.99.99.99
            cfg = cfgs["te_s_r1"]
            cfgs["te_s_r1"] = cfg.replace("10.20.0.10", "10.99.99.99")
            res = audit_oracle.check(cfgs, invs)
            if not all(res.values()):
                detection_layer = "Oracle (Unreachable Next Hop)"
                error_msg = f"Failed invariants: {[k for k, v in res.items() if not v]}"

        elif c_id == "C04_vrf_forwarding_swap":
            # Reassign an interface from CORP to GUEST on te_s_r1
            cfg = cfgs["te_s_r1"]
            cfgs["te_s_r1"] = cfg.replace("ip vrf forwarding CORP", "ip vrf forwarding GUEST")
            res = audit_oracle.check(cfgs, invs)
            if not all(res.values()):
                detection_layer = "Oracle (VRF Boundary Violation)"
                error_msg = f"Failed invariants: {[k for k, v in res.items() if not v]}"

        elif c_id == "C05_omitted_access_group_in":
            # Delete ip access-group EDGE_IN in from firewall
            cfg = cfgs["te_s_fw1"]
            cfgs["te_s_fw1"] = cfg.replace(" ip access-group EDGE_IN in\n", "")
            res = audit_oracle.check(cfgs, invs)
            if not all(res.values()):
                detection_layer = "Oracle (Firewall ACL bypassed)"
                error_msg = f"Failed invariants: {[k for k, v in res.items() if not v]}"

        elif c_id == "C06_corrupted_interface_netmask":
            # Change subnet mask on te_s_r1 from /24 to /30
            cfg = cfgs["te_s_r1"]
            cfgs["te_s_r1"] = cfg.replace("255.255.255.0", "255.255.255.252")
            res = audit_oracle.check(cfgs, invs)
            if not all(res.values()):
                detection_layer = "Oracle (Subnet Mismatch)"
                error_msg = f"Failed invariants: {[k for k, v in res.items() if not v]}"

        elif c_id == "C07_action_permit_deny_swap":
            # Swap permit tcp any any eq 80 to deny tcp any any eq 80 on te_s_r1
            cfg = cfgs["te_s_r1"]
            cfgs["te_s_r1"] = cfg.replace("permit tcp any any eq 80", "deny tcp any any eq 80")
            res = audit_oracle.check(cfgs, invs)
            if not all(res.values()):
                detection_layer = "Oracle (Denied Legitimate Service)"
                error_msg = f"Failed invariants: {[k for k, v in res.items() if not v]}"

        elif c_id == "C08_static_route_vrf_omission":
            # Drop VRF tag on static route (ip route vrf CORP -> ip route)
            cfg = cfgs["te_s_r1"]
            cfgs["te_s_r1"] = cfg.replace("ip route vrf CORP 10.21.102.0", "ip route 10.21.102.0")
            res = audit_oracle.check(cfgs, invs)
            if not all(res.values()):
                detection_layer = "Oracle (Route In Global Table Instead of VRF)"
                error_msg = f"Failed invariants: {[k for k, v in res.items() if not v]}"

        elif c_id == "C09_unrecognized_device_in_fence":
            # Device fence has misspelled name te_s_r1_wrong
            md = f"```te_s_r1_wrong\n{cfgs['te_s_r1']}\n```"
            parsed = parse_model_output(md)
            resolved = resolve_devices(parsed.devices, spec.inventory)
            if not resolved.is_ok or len(resolved.errors) > 0:
                detection_layer = "Resolver (Device Name Resolution Error)"
                error_msg = f"Resolver errors: {resolved.errors}"

        elif c_id == "C10_direction_in_out_swap":
            # Swap ip access-group EDGE_IN in to out on te_s_fw1
            cfg = cfgs["te_s_fw1"]
            cfgs["te_s_fw1"] = cfg.replace("ip access-group EDGE_IN in", "ip access-group EDGE_IN out")
            res = audit_oracle.check(cfgs, invs)
            if not all(res.values()):
                detection_layer = "Oracle (Inbound Security Policies Unapplied)"
                error_msg = f"Failed invariants: {[k for k, v in res.items() if not v]}"

        is_detected = detection_layer is not None
        if is_detected:
            detected_count += 1
        status_str = "DETECTED" if is_detected else "MISSED"
        print(f"  [{status_str}] {c_id}: Layer={detection_layer}")
        if error_msg:
            print(f"           Detail: {error_msg}")

    print(f"\nNew Corruptions Detection Rate: {detected_count}/10 ({detected_count * 10.0:.1f}%)")

if __name__ == "__main__":
    main()
