"""Tests for agent/analysis/config_diff.py.

Table-driven tests covering additions, removals, unrelated line removals,
dropped ACL attachments, interface modifications, and prefix filters.
"""

import pytest
from agent.analysis.config_diff import ConfigDiffResult, diff_configs
from agent.contract.models import Intent, IntentType


BASE_CONFIG = """
hostname r1
!
ip access-list extended EDGE_IN
 permit tcp any host 10.0.0.1 eq 22
 permit ip 10.1.0.0 0.0.255.255 any
!
ip access-list extended MGMT_ACL
 permit tcp 192.168.1.0 0.0.0.255 any eq 443
!
interface GigabitEthernet0/0
 description WAN to ISP
 ip address 203.0.113.1 255.255.255.0
 ip access-group EDGE_IN in
!
interface GigabitEthernet0/1
 description LAN Internal
 ip address 10.0.0.254 255.255.255.0
!
router bgp 65001
 neighbor 203.0.113.2 remote-as 65002
"""

TEST_CASES = [
    # 1. No changes
    {
        "name": "identical_configs",
        "input": BASE_CONFIG,
        "output": BASE_CONFIG,
        "intent": Intent(
            id="i1",
            sequence_id="s1",
            step=0,
            type=IntentType.ACL_PERMIT,
            params={"acl": "EDGE_IN", "port": 80},
            text="Allow HTTP",
            adds_invariant="inv1",
        ),
        "expected_removed": [],
        "expected_added": [],
        "expected_unrelated": [],
    },
    # 2. Legitimate addition only
    {
        "name": "legitimate_addition",
        "input": BASE_CONFIG,
        "output": BASE_CONFIG + "\n permit tcp any host 10.0.0.1 eq 80",
        "intent": Intent(
            id="i2",
            sequence_id="s1",
            step=1,
            type=IntentType.ACL_PERMIT,
            params={"acl": "EDGE_IN", "port": 80},
            text="Allow HTTP",
            adds_invariant="inv2",
        ),
        "expected_removed": [],
        "expected_added": ["permit tcp any host 10.0.0.1 eq 80"],
        "expected_unrelated": [],
    },
    # 3. Dropped ACL attachment (unrelated to prefix intent)
    {
        "name": "dropped_acl_attachment",
        "input": BASE_CONFIG,
        "output": BASE_CONFIG.replace(" ip access-group EDGE_IN in\n", ""),
        "intent": Intent(
            id="i3",
            sequence_id="s1",
            step=2,
            type=IntentType.PREFIX_FILTER,
            params={"interface": "GigabitEthernet0/1", "prefix": "10.0.0.0/24"},
            text="Filter prefixes on GigabitEthernet0/1",
            adds_invariant="inv3",
        ),
        "expected_removed": ["ip access-group EDGE_IN in"],
        "expected_added": [],
        "expected_unrelated": ["ip access-group EDGE_IN in"],
    },
    # 4. Legitimate ACL rule replacement (removal contains ACL name/token)
    {
        "name": "acl_rule_replacement",
        "input": BASE_CONFIG,
        "output": BASE_CONFIG.replace(
            " permit tcp any host 10.0.0.1 eq 22",
            " permit tcp 10.10.10.0 0.0.0.255 host 10.0.0.1 eq 22",
        ),
        "intent": Intent(
            id="i4",
            sequence_id="s1",
            step=3,
            type=IntentType.ACL_PERMIT,
            params={"acl": "EDGE_IN", "dst": "10.0.0.1", "port": 22},
            text="Restrict SSH on EDGE_IN to 10.10.10.0/24",
            adds_invariant="inv4",
        ),
        "expected_removed": ["permit tcp any host 10.0.0.1 eq 22"],
        "expected_added": ["permit tcp 10.10.10.0 0.0.0.255 host 10.0.0.1 eq 22"],
        "expected_unrelated": [],
    },
    # 5. Unrelated BGP neighbor removal during ACL change
    {
        "name": "dropped_bgp_neighbor",
        "input": BASE_CONFIG,
        "output": BASE_CONFIG.replace(" neighbor 203.0.113.2 remote-as 65002\n", ""),
        "intent": Intent(
            id="i5",
            sequence_id="s1",
            step=4,
            type=IntentType.ACL_PERMIT,
            params={"acl": "EDGE_IN", "port": 443},
            text="Add HTTPS to EDGE_IN",
            adds_invariant="inv5",
        ),
        "expected_removed": ["neighbor 203.0.113.2 remote-as 65002"],
        "expected_added": [],
        "expected_unrelated": ["neighbor 203.0.113.2 remote-as 65002"],
    },
    # 6. Unrelated interface description removal
    {
        "name": "dropped_interface_description",
        "input": BASE_CONFIG,
        "output": BASE_CONFIG.replace(" description LAN Internal\n", ""),
        "intent": Intent(
            id="i6",
            sequence_id="s1",
            step=5,
            type=IntentType.ACL_DENY,
            params={"acl": "MGMT_ACL", "src": "192.168.1.50"},
            text="Block 192.168.1.50 on MGMT_ACL",
            adds_invariant="inv6",
        ),
        "expected_removed": ["description LAN Internal"],
        "expected_added": [],
        "expected_unrelated": ["description LAN Internal"],
    },
    # 7. Related interface change (interface token matches)
    {
        "name": "interface_ip_change",
        "input": BASE_CONFIG,
        "output": BASE_CONFIG.replace(
            " ip address 10.0.0.254 255.255.255.0",
            " ip address 10.0.0.1 255.255.255.0",
        ),
        "intent": Intent(
            id="i7",
            sequence_id="s1",
            step=6,
            type=IntentType.PREFIX_FILTER,
            params={"interface": "GigabitEthernet0/1", "prefix": "10.0.0.0/24"},
            text="Update IP on GigabitEthernet0/1",
            adds_invariant="inv7",
        ),
        "expected_removed": ["ip address 10.0.0.254 255.255.255.0"],
        "expected_added": ["ip address 10.0.0.1 255.255.255.0"],
        "expected_unrelated": [],
    },
    # 8. Unrelated removal with dictionary intent and explicit kwargs
    {
        "name": "dict_intent_with_explicit_kwargs",
        "input": BASE_CONFIG,
        "output": BASE_CONFIG.replace("hostname r1\n", " hostname r1-core\n"),
        "intent": {"params": {"acl": "EDGE_IN"}},
        "expected_removed": ["hostname r1"],
        "expected_added": ["hostname r1-core"],
        "expected_unrelated": ["hostname r1"],
    },
    # 9. No intent provided - all removals considered unrelated
    {
        "name": "no_intent_removals_all_unrelated",
        "input": BASE_CONFIG,
        "output": BASE_CONFIG.replace("hostname r1\n", ""),
        "intent": None,
        "expected_removed": ["hostname r1"],
        "expected_added": [],
        "expected_unrelated": ["hostname r1"],
    },
    # 10. Multiple removals with mixed related and unrelated lines
    {
        "name": "mixed_removals",
        "input": BASE_CONFIG,
        "output": BASE_CONFIG.replace(
            " permit tcp any host 10.0.0.1 eq 22\n", ""
        ).replace(
            " neighbor 203.0.113.2 remote-as 65002\n", ""
        ),
        "intent": Intent(
            id="i10",
            sequence_id="s1",
            step=7,
            type=IntentType.ACL_PERMIT,
            params={"acl": "EDGE_IN", "dst": "10.0.0.1"},
            text="Update EDGE_IN",
            adds_invariant="inv10",
        ),
        "expected_removed": [
            "permit tcp any host 10.0.0.1 eq 22",
            "neighbor 203.0.113.2 remote-as 65002",
        ],
        "expected_added": [],
        "expected_unrelated": ["neighbor 203.0.113.2 remote-as 65002"],
    },
]


@pytest.mark.parametrize("case", TEST_CASES, ids=[c["name"] for c in TEST_CASES])
def test_diff_configs_table(case):
    res: ConfigDiffResult = diff_configs(
        input_config=case["input"],
        output_config=case["output"],
        intent=case["intent"],
    )

    assert res.removed_lines == case["expected_removed"]
    assert res.added_lines == case["expected_added"]
    assert res.unrelated_removed_lines == case["expected_unrelated"]
    if case["expected_unrelated"]:
        assert res.has_unrelated_removals is True
    else:
        assert res.has_unrelated_removals is False
