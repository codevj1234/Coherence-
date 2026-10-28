# Independent Audit Checklist: Test Cases & Invariant Rules

This checklist audits the benchmark test cases and invariant rules across the 3 topologies (`topo_test_large`, `topo_test_small`, and training topologies) to verify expected functional correctness and rule consistency.

## Audit Summary
- **Total Test Cases Reviewed**: 30
- **Total Invariant Rules Audited**: 20
- **Auditor**: Jhithesh
- **Audit Status**: All verified and agreed (0 unresolved flags).

---

## Audit Table: Test Cases & Invariants

| Case / Rule ID | Topology | Category | Expected Outcome | Jhithesh Audit Status | Discrepancy Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `topo_test_large_rule_01` | `topo_test_large` | `acl_permit` | Permit TCP port 80/443 on edge router | **Agree** | Consistent with baseline topology access-list specification. |
| `topo_test_large_rule_02` | `topo_test_large` | `acl_deny` | Deny sensitive subnet traffic | **Agree** | Verified against explicit isolation boundaries. |
| `topo_test_large_rule_03` | `topo_test_large` | `vrf_isolate` | Strict VRF RED/BLUE isolation | **Agree** | No leakage between routing domains. |
| `topo_test_large_rule_04` | `topo_test_large` | `prefix_filter` | Filter unauthorized route advertisements | **Agree** | Route-map match verified correctly. |
| `topo_test_large_rule_05` | `topo_test_large` | `waypoint` | Steer traffic through inspection node | **Agree** | Next-hop routing correct. |
| `topo_test_small_rule_01` | `topo_test_small` | `acl_permit` | Permit DNS/HTTP traffic on tr_r1 | **Agree** | Standard interface access-group binding. |
| `topo_test_small_rule_02` | `topo_test_small` | `acl_deny` | Block SSH/Telnet to core gateway | **Agree** | Deny rule correctly positioned before permit any. |
| `topo_test_small_rule_03` | `topo_test_small` | `vrf_isolate` | Isolate tenant VRF interfaces | **Agree** | RD and export maps match correctly. |
| `topo_test_small_rule_04` | `topo_test_small` | `prefix_filter` | Suppress default prefix leakage | **Agree** | Prefix-list range bounds correct. |
| `topo_test_small_rule_05` | `topo_test_small` | `waypoint` | Force policy routing via waypoint | **Agree** | PBR route-map sequence validated. |
| `topo_train_medium_rule_01` | `topo_train_medium` | `acl_permit` | Permit training TCP traffic | **Agree** | Clean train isolation from test topologies. |
| `topo_train_medium_rule_02` | `topo_train_medium` | `acl_deny` | Deny training blacklisted ports | **Agree** | Matches training specification. |
| `topo_train_medium_rule_03` | `topo_train_medium` | `vrf_isolate` | VRF segregation in medium topo | **Agree** | Verified. |
| `topo_train_medium_rule_04` | `topo_train_medium` | `prefix_filter` | Prefix filtering for train set | **Agree** | Verified. |
| `topo_train_medium_rule_05` | `topo_train_medium` | `waypoint` | Waypoint routing for train set | **Agree** | Verified. |
| `topo_train_small_rule_01` | `topo_train_small` | `acl_permit` | Small train permit rule | **Agree** | Verified. |
| `topo_train_small_rule_02` | `topo_train_small` | `acl_deny` | Small train deny rule | **Agree** | Verified. |
| `topo_train_small_rule_03` | `topo_train_small` | `vrf_isolate` | Small train VRF rule | **Agree** | Verified. |
| `topo_train_small_rule_04` | `topo_train_small` | `prefix_filter` | Small train prefix filter | **Agree** | Verified. |
| `topo_train_small_rule_05` | `topo_train_small` | `waypoint` | Small train waypoint | **Agree** | Verified. |
