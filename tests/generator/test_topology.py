"""Tests for topology generation, catalog, determinism, and train/test disjointness."""

import ipaddress
import pytest
from agent.contract.models import Intent, IntentType
from agent.prompts.parser import parse_model_output
from agent.prompts.resolve import resolve_devices
from generator.intents.templates import format_gold_patch_as_model_output
from generator.oracle.reference import ReferenceOracle
from generator.topology.catalog import TOPOLOGIES, list_topologies
from generator.topology.generator import generate_topology_configs


def test_topology_catalog_completeness():
    """Verify that catalog contains the 4 required topologies (2 train, 2 test)."""
    assert len(TOPOLOGIES) == 4
    train_topos = list_topologies(split="train")
    test_topos = list_topologies(split="test")
    assert len(train_topos) == 2
    assert len(test_topos) == 2

    train_ids = {t.id for t in train_topos}
    test_ids = {t.id for t in test_topos}
    assert train_ids == {"topo_train_small", "topo_train_medium"}
    assert test_ids == {"topo_test_small", "topo_test_large"}


def test_topology_determinism():
    """Verify that generating topology configs is strictly deterministic."""
    for topo_id, spec in TOPOLOGIES.items():
        configs_1 = generate_topology_configs(spec)
        configs_2 = generate_topology_configs(spec)
        assert configs_1 == configs_2, f"Topology {topo_id} is non-deterministic!"


def test_baseline_invariants_pass():
    """Verify that 100% of baseline invariants pass for all 4 topologies using ReferenceOracle."""
    oracle = ReferenceOracle()
    for topo_id, spec in TOPOLOGIES.items():
        assert len(spec.baseline_invariants) >= 20, f"{topo_id} must have at least 20 baseline invariants"
        configs = generate_topology_configs(spec)
        results = oracle.check(configs, spec.baseline_invariants)
        failed = [inv_id for inv_id, ok in results.items() if not ok]
        assert not failed, f"Topology {topo_id} failed baseline invariants: {failed}"


def test_train_test_disjointness():
    """Verify train and test topologies share no IDs, node names, or addressing."""
    train_topos = list_topologies(split="train")
    test_topos = list_topologies(split="test")

    train_ids = {t.id for t in train_topos}
    test_ids = {t.id for t in test_topos}
    assert train_ids.isdisjoint(test_ids)

    train_nodes = {node for t in train_topos for node in t.nodes}
    test_nodes = {node for t in test_topos for node in t.nodes}
    assert train_nodes.isdisjoint(test_nodes)

    train_subnets = [
        ipaddress.ip_network(hsub.network_cidr)
        for t in train_topos
        for node in t.nodes.values()
        for hsub in node.host_subnets
    ]
    test_subnets = [
        ipaddress.ip_network(hsub.network_cidr)
        for t in test_topos
        for node in t.nodes.values()
        for hsub in node.host_subnets
    ]

    for tr_net in train_subnets:
        for te_net in test_subnets:
            assert not tr_net.overlaps(te_net), f"Train net {tr_net} overlaps test net {te_net}!"


def test_topology_configs_roundtrip_parser_and_resolver():
    """Verify generated topology configs parse and resolve with zero errors and zero warnings."""
    for topo_id, spec in TOPOLOGIES.items():
        configs = generate_topology_configs(spec)
        fenced_text = format_gold_patch_as_model_output(configs)

        parsed = parse_model_output(fenced_text)
        assert len(parsed.devices) == len(configs)
        assert parsed.is_ok

        resolved = resolve_devices(parsed.devices, spec.inventory)
        assert resolved.is_ok
        assert len(resolved.errors) == 0
        assert len(resolved.warnings) == 0
        assert set(resolved.devices.keys()) == set(spec.nodes.keys())
        for dev, cfg in resolved.devices.items():
            assert cfg == configs[dev].strip()
