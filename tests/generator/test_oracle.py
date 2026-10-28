"""Tests for Verification Oracle implementations: ReferenceOracle, MockOracle, and BatfishOracle."""

import pytest
from agent.contract.models import Invariant, InvariantKind
from generator.oracle.base import Oracle
from generator.oracle.batfish import BatfishNotAvailableError, BatfishOracle
from generator.oracle.mock import MockOracle
from generator.oracle.reference import ReferenceOracle
from generator.topology.catalog import get_topology
from generator.topology.generator import generate_topology_configs


def test_mock_oracle():
    """Verify MockOracle passes all invariants unconditionally."""
    oracle = MockOracle()
    invs = [
        Invariant(id="inv1", kind=InvariantKind.REACH, source="10.0.0.1", destination="10.0.0.2", headers={}, created_at_step=0),
        Invariant(id="inv2", kind=InvariantKind.ISOLATE, source="10.0.0.1", destination="10.0.0.3", headers={}, created_at_step=0),
    ]
    results = oracle.check({}, invs)
    assert results == {"inv1": True, "inv2": True}
    detail = oracle.explain_failure({}, invs[0])
    assert detail.passed is True


def test_batfish_oracle_skip():
    """Verify BatfishOracle handles absence of Batfish server cleanly."""
    oracle = BatfishOracle()
    if not oracle.is_available:
        with pytest.raises(BatfishNotAvailableError):
            oracle.check({}, [])
    else:
        # If batfish happens to be running, check returns dict
        res = oracle.check({}, [])
        assert isinstance(res, dict)


def test_reference_oracle_acl_permit_deny():
    """Verify ReferenceOracle correctly respects ACL permit and deny rules."""
    spec = get_topology("topo_test_small")
    configs = generate_topology_configs(spec)
    oracle = ReferenceOracle()

    # In topo_test_small, baseline has deny tcp any any eq 22 on edge routers
    # te_s_r1 hosts 10.21.1.10
    # te_s_r5 hosts 10.21.5.10
    inv_ssh = Invariant(
        id="test_ssh_denied",
        kind=InvariantKind.ISOLATE,
        source="10.21.1.10",
        destination="10.21.5.10",
        headers={"protocol": "tcp", "dst_port": 22},
        created_at_step=0,
    )
    res = oracle.check(configs, [inv_ssh])
    assert res["test_ssh_denied"] is True

    # If we claim SSH should REACH, it should fail
    inv_ssh_reach = Invariant(
        id="test_ssh_reach_should_fail",
        kind=InvariantKind.REACH,
        source="10.21.1.10",
        destination="10.21.5.10",
        headers={"protocol": "tcp", "dst_port": 22},
        created_at_step=0,
    )
    res_reach = oracle.check(configs, [inv_ssh_reach])
    assert res_reach["test_ssh_reach_should_fail"] is False

    # Check failure explanation
    detail = oracle.explain_failure(configs, inv_ssh_reach)
    assert detail is not None
    assert detail.passed is False
    assert detail.invariant_id == "test_ssh_reach_should_fail"
    assert "dropped by ACL" in detail.reason or "denied" in detail.reason.lower()


def test_reference_oracle_waypoint():
    """Verify ReferenceOracle validates waypoint traversal."""
    spec = get_topology("topo_test_small")
    configs = generate_topology_configs(spec)
    oracle = ReferenceOracle()

    # Baseline traffic from te_s_r1 to te_s_r5 passes through te_s_fw1
    inv_wp_pass = Invariant(
        id="test_wp_fw1_pass",
        kind=InvariantKind.WAYPOINT,
        source="10.21.1.10",
        destination="10.21.5.10",
        headers={"protocol": "tcp", "dst_port": 80, "waypoint": "te_s_fw1"},
        created_at_step=0,
    )
    res = oracle.check(configs, [inv_wp_pass])
    assert res["test_wp_fw1_pass"] is True

    # But traffic from te_s_r1 to te_s_r5 does not pass through an unrelated node (e.g. non-existent or wrong node)
    inv_wp_fail = Invariant(
        id="test_wp_fake_fail",
        kind=InvariantKind.WAYPOINT,
        source="10.21.1.10",
        destination="10.21.5.10",
        headers={"protocol": "tcp", "dst_port": 80, "waypoint": "nonexistent_node"},
        created_at_step=0,
    )
    res_fail = oracle.check(configs, [inv_wp_fail])
    assert res_fail["test_wp_fake_fail"] is False


def test_reference_oracle_vrf_isolation():
    """Verify ReferenceOracle strictly enforces VRF isolation."""
    spec = get_topology("topo_test_small")
    configs = generate_topology_configs(spec)
    oracle = ReferenceOracle()

    # CORP host (10.21.101.10) to GUEST host (10.21.201.10) must be isolated
    inv_vrf_isolate = Invariant(
        id="test_vrf_corp_guest_isolate",
        kind=InvariantKind.ISOLATE,
        source="10.21.101.10",
        destination="10.21.201.10",
        headers={"protocol": "ip"},
        created_at_step=0,
    )
    res = oracle.check(configs, [inv_vrf_isolate])
    assert res["test_vrf_corp_guest_isolate"] is True
