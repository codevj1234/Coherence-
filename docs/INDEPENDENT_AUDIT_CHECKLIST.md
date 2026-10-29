# Independent Audit Checklist: Test Cases & Invariant Rules (Guard Package Inspection)

This checklist provides the formal technical audit of the 30 hand-made test cases defined in `guard/cuc/oracle.py` and the 15 baseline invariant policies defined in `guard/cuc/invariants.py` over the Abilene/Topology Zoo testbed (`guard/cuc/topology.py`).

## Audit Summary
- **Total Test Cases Audited**: 30 (`S01`–`S10` safe, `B01`–`B10` breaking, `U01`–`U10` subtle)
- **Total Invariant Policies Audited**: 15 (`R01`–`R08` reachability, `I01`–`I06` isolation, `W01` waypoint)
- **Auditor**: Jhithesh
- **Audit Methodology**: Line-by-line inspection of ACL sequence numbers, prefix filters, wildcard masks, and VRF routing logic.

---

## Part 1: Baseline Invariant Policies (`guard/cuc/invariants.py`)

| Policy ID | Kind | Source -> Destination | Headers / Ports | Expected Batfish/Oracle Verdict | Jhithesh Audit Status | Technical Notes |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `R01` | `reach` | `10.1.3.0/24` -> `10.1.1.0/24` | `tcp/22` | Must be reachable | **Agree** | Permitted by `TO_E1` line 10 on router `E1`. |
| `R02` | `reach` | `10.1.2.0/24` -> `10.1.1.0/24` | `tcp/80` | Must be reachable | **Agree** | Permitted by `TO_E1` line 20 on router `E1`. |
| `R03` | `reach` | `10.1.1.0/24` -> `10.1.2.0/24` | `tcp/80` | Must be reachable | **Agree** | Permitted by `TO_E2` line 10 on router `E2`. |
| `R04` | `reach` | `10.1.1.0/24` -> `10.1.3.0/24` | `tcp/443` | Must be reachable | **Agree** | Permitted by `TO_E3` catch-all rule on `E3`. |
| `R05` | `reach` | `10.1.2.0/24` -> `10.1.3.0/24` | `tcp/443` | Must be reachable | **Agree** | Permitted by `TO_E3` catch-all rule on `E3`. |
| `R06` | `reach` | `10.1.1.0/24` -> `10.1.3.0/24` | `tcp/80` | Must be reachable | **Agree** | Permitted by `TO_E3` catch-all rule on `E3`. |
| `R07` | `reach` | `10.1.1.0/24` -> `10.1.4.0/24` | `tcp/80` | Must be reachable | **Agree** | Permitted by `TO_E4` line 10 on firewall `FW`. |
| `R08` | `reach` | `10.1.3.0/24` -> `10.1.4.0/24` | `tcp/443` | Must be reachable | **Agree** | Permitted by `TO_E4` line 20 on firewall `FW`. |
| `I01` | `isolate` | `10.1.2.0/24` -> `10.1.3.0/24` | `tcp/22` | Must be blocked | **Agree** | Blocked by `TO_E3` line 10 (`deny tcp 10.1.2.0 0.0.0.255 10.1.3.0 0.0.0.255 eq 22`). |
| `I02` | `isolate` | `10.1.4.0/24` -> `10.1.3.0/24` | `ip` (all) | Must be blocked | **Agree** | Blocked by `TO_E3` line 20 (`deny ip 10.1.4.0 0.0.0.255 10.1.3.0 0.0.0.255`). |
| `I03` | `isolate` | `10.1.2.0/24` -> `10.1.1.0/24` | `tcp/22` | Must be blocked | **Agree** | Blocked by implicit deny on `TO_E1`. |
| `I04` | `isolate` | `10.1.3.0/24` -> `10.1.2.0/24` | `tcp/80` | Must be blocked | **Agree** | Blocked by implicit deny on `TO_E2`. |
| `I05` | `isolate` | `10.1.2.0/24` -> `10.1.4.0/24` | `tcp/80` | Must be blocked | **Agree** | Blocked by implicit deny on `TO_E4`. |
| `I06` | `isolate` | `10.1.4.0/24` -> `10.1.1.0/24` | `tcp/22` | Must be blocked | **Agree** | Blocked by implicit deny on `TO_E1`. |
| `W01` | `waypoint` | `10.1.1.0/24` -> `10.1.4.0/24` | `tcp/80` | Must pass via `FW` | **Agree** | Topology routing and firewall policy enforce transit through `FW`. |

---

## Part 2: Oracle Test Cases (`guard/cuc/oracle.py`)

| Case ID | Label | Description & Human Reasoning (`guard/cuc/oracle.py`) | Expected Broken Invariants | Jhithesh Audit Status | Technical Notes |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `S01`–`S10` | `safe` | Non-overlapping additions (e.g., permitting tcp/443 on unrelated interfaces, explicit denies duplicating implicit denies, unattached ACLs). | `{}` (None) | **Agree** | Correctly evaluated; no existing invariants affected. |
| `B01`–`B10` | `breaking` | Destructive edits (e.g., removing `permit ip any any` on `TO_E3`, shutting uplink interfaces, removing core OSPF sessions). | Various (`R04`, `R06`, `R01`, `R07`, etc.) | **Agree** | Correctly identified by reference and Batfish oracles as invariant regressions. |
| `U01`–`U10` | `subtle` | Order-dependent rule changes and ACL shadowing (e.g., placing broader deny rules above permits, renumbering catch-all lines, inbound vs outbound ACL bindings). | Varies by rule ordering | **Agree** | Evaluated successfully against Cisco IOS ACL matching semantics (first-match processing). |
