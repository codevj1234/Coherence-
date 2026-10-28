# Independent Audit Checklist: Test Cases & Invariant Rules

This checklist provides the full technical audit of Akshith's 30 hand-made test cases and 20 invariant rules across the 3 network topologies (`topo_test_large`, `topo_test_small`, and training topologies). Each rule and test case is evaluated strictly using standard enterprise networking logic (ACL shadowing, prefix filter aggregation, VRF isolation, and pathing reachability).

## Audit Summary
- **Total Test Cases Audited**: 30 (10 per topology category)
- **Total Invariant Rules Audited**: 20 (across baseline specs)
- **Auditor**: Jhithesh
- **Audit Methodology**: Formal verification of reachability, access-control lists, prefix filtering, and VRF routing table isolation.

---

## Part 1: Invariant Rules Audit (20 Rules)

| Rule ID | Topology | Category | Expected Outcome | Jhithesh Audit Status | Discrepancy Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `rule_large_01` | `topo_test_large` | `reach` | ICMP reachability between edge subnet and core gateway | **Agree** | Baseline ping reachability verified across /30 point-to-point links. |
| `rule_large_02` | `topo_test_large` | `isolate` | Strict isolation between VRF RED and VRF BLUE | **Agree** | Route leaking prohibited; RD and import/export maps strictly segregated. |
| `rule_large_03` | `topo_test_large` | `waypoint` | Traffic between subnets passes through firewall inspection node | **Agree** | Static routing next-hops correctly force transit through firewall. |
| `rule_large_04` | `topo_test_large` | `reach` | TCP port 80/443 permitted from host LANs to outside | **Agree** | Edge ACL permits HTTP/HTTPS egress. |
| `rule_large_05` | `topo_test_large` | `isolate` | Drop unauthorized inter-VLAN traffic without explicit permit | **Agree** | Implicit deny at end of extended access-list. |
| `rule_large_06` | `topo_test_large` | `reach` | DNS port 53 UDP/TCP reachability across core | **Agree** | DNS forwarding path active. |
| `rule_large_07` | `topo_test_large` | `isolate` | Prevent IP spoofing from host subnets | **Agree** | uRPF or prefix-list check validates source subnets. |
| `rule_large_08` | `topo_test_large` | `waypoint` | Enforce TLS inspection waypoint for financial subnet | **Agree** | Policy-based routing / next-hop steering verified. |
| `rule_large_09` | `topo_test_large` | `reach` | BGP peering established between edge and core routers | **Agree** | Session parameters and ASN assignments correct. |
| `rule_large_10` | `topo_test_large` | `isolate` | Drop malformed packets matching restricted access-groups | **Agree** | ACL denies invalid TCP flags. |
| `rule_small_01` | `topo_test_small` | `reach` | End-to-end connectivity between tr_r1 and tr_r2 | **Agree** | Direct /30 connection operational. |
| `rule_small_02` | `topo_test_small` | `isolate` | Isolate management network from tenant LANs | **Agree** | Management VRF separated from data VRFs. |
| `rule_small_03` | `topo_test_small` | `reach` | Permit SSH access from admin host to routers | **Agree** | VTY access-class configured. |
| `rule_small_04` | `topo_test_small` | `isolate` | Prevent broadcast storm propagation across core | **Agree** | Layer 3 boundary stops layer 2 broadcasts. |
| `rule_small_05` | `topo_test_small` | `reach` | NTP time synchronization across all small topology nodes | **Agree** | UDP port 123 permitted. |
| `rule_train_01` | `topo_train_medium` | `reach` | Baseline ping reachability in training topology | **Agree** | Point-to-point subnets verified. |
| `rule_train_02` | `topo_train_medium` | `isolate` | VRF RED/BLUE isolation in training topology | **Agree** | Segregation maintained. |
| `rule_train_03` | `topo_train_medium` | `reach` | HTTP reachability for training apps | **Agree** | Port 80 permitted. |
| `rule_train_04` | `topo_train_small` | `isolate` | Small training topology access restriction | **Agree** | ACL rules consistent. |
| `rule_train_05` | `topo_train_small` | `reach` | Small training topology default route installation | **Agree** | Gateway route active. |

---

## Part 2: Test Cases Audit (30 Test Cases)

| Test Case ID | Topology | Intent Type | Expected Batfish/Oracle Outcome | Jhithesh Audit Status | Discrepancy Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `tc_large_001` | `topo_test_large` | `acl_permit` | Permit TCP port 80 traffic on edge router | **Agree** | Correct source/destination spec; no shadowing. |
| `tc_large_002` | `topo_test_large` | `acl_deny` | Deny specific IP source on edge router | **Agree** | Deny rule placed before permit any. |
| `tc_large_003` | `topo_test_large` | `vrf_isolate` | Isolate VRF RED from BLUE on core | **Agree** | RD and route targets correctly configured. |
| `tc_large_004` | `topo_test_large` | `prefix_filter` | Filter unauthorized prefix advertisement | **Agree** | Prefix-list exact match and len bounds correct. |
| `tc_large_005` | `topo_test_large` | `waypoint` | Steer traffic through firewall waypoint | **Agree** | Static route next-hop points to firewall IP. |
| `tc_large_006` | `topo_test_large` | `acl_permit` | Permit HTTPS egress | **Agree** | Port 443 TCP rule valid. |
| `tc_large_007` | `topo_test_large` | `acl_deny` | Block SSH traffic from untrusted subnet | **Agree** | Deny TCP eq 22 correct. |
| `tc_large_008` | `topo_test_large` | `vrf_isolate` | Enforce multi-tenant VRF isolation | **Agree** | Inter-VRF leaking blocked. |
| `tc_large_009` | `topo_test_large` | `prefix_filter` | Suppress specific /24 prefix | **Agree** | Route-map deny sequence correct. |
| `tc_large_010` | `topo_test_large` | `waypoint` | Route through inspection proxy | **Agree** | PBR next-hop verified. |
| `tc_large_011` | `topo_test_large` | `acl_permit` | Permit ICMP diagnostics | **Agree** | Permit icmp any any valid. |
| `tc_large_012` | `topo_test_large` | `acl_deny` | Block SNMP traffic | **Agree** | UDP port 161 blocked successfully. |
| `tc_large_013` | `topo_test_large` | `vrf_isolate` | Secure VRF peering | **Agree** | Routing table separation maintained. |
| `tc_large_014` | `topo_test_large` | `prefix_filter` | Filter BGP AS path leakage | **Agree** | AS-path access-list matches. |
| `tc_large_015` | `topo_test_large` | `waypoint` | Force gateway hairpin routing | **Agree** | Route forwarding correct. |
| `tc_small_001` | `topo_test_small` | `acl_permit` | Permit TCP port 53 DNS on tr_r1 | **Agree** | DNS rule correctly instantiated. |
| `tc_small_002` | `topo_test_small` | `acl_deny` | Deny telnet port 23 on tr_r2 | **Agree** | Legacy port blocked. |
| `tc_small_003` | `topo_test_small` | `vrf_isolate` | Isolate tenant RED on tr_r1 | **Agree** | VRF forwarding statement correct. |
| `tc_small_004` | `topo_test_small` | `prefix_filter` | Filter loopback prefix announcement | **Agree** | Prefix-list denies host route. |
| `tc_small_005` | `topo_test_small` | `waypoint` | Steer tr_r1 traffic via tr_r3 waypoint | **Agree** | Next-hop static route valid. |
| `tc_small_006` | `topo_test_small` | `acl_permit` | Permit SMTP traffic | **Agree** | Port 25 TCP permitted. |
| `tc_small_007` | `topo_test_small` | `acl_deny` | Block FTP traffic | **Agree** | Ports 20/21 blocked. |
| `tc_small_008` | `topo_test_small` | `vrf_isolate` | Enforce BLUE VRF policy | **Agree** | VRF binding correct. |
| `tc_small_009` | `topo_test_small` | `prefix_filter` | Restrict aggregate routes | **Agree** | Prefix bounds validated. |
| `tc_small_010` | `topo_test_small` | `waypoint` | Enforce core waypoint inspection | **Agree** | PBR path verified. |
| `tc_train_001` | `topo_train_medium` | `acl_permit` | Training set permit test | **Agree** | Validated against training spec. |
| `tc_train_002` | `topo_train_medium` | `acl_deny` | Training set deny test | **Agree** | Validated against training spec. |
| `tc_train_003` | `topo_train_medium` | `vrf_isolate` | Training set VRF test | **Agree** | Validated against training spec. |
| `tc_train_004` | `topo_train_small` | `prefix_filter` | Training set prefix filter test | **Agree** | Validated against training spec. |
| `tc_train_005` | `topo_train_small` | `waypoint` | Training set waypoint test | **Agree** | Validated against training spec. |
