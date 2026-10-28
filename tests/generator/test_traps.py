"""Tests for trap generation, automated proofs, and sidecar metadata."""

import random
import pytest
from agent.contract.models import Invariant, InvariantKind
from generator.oracle.reference import ReferenceOracle
from generator.topology.catalog import get_topology
from generator.topology.generator import generate_topology_configs
from generator.traps.catalog import generate_trap_step
from generator.traps.isolation_leak import build_isolation_leak_trap
from generator.traps.prefix_overlap import build_prefix_overlap_trap
from generator.traps.shadowing import build_shadowing_trap


def test_shadowing_trap_automated_proof():
    """Verify shadowing trap naive edit provably breaks an earlier invariant, while gold edit passes."""
    spec = get_topology("topo_test_small")
    configs = generate_topology_configs(spec)
    oracle = ReferenceOracle()
    rng = random.Random(42)

    # Baseline invariants have ISOLATE invariants (port 22 SSH)
    res = build_shadowing_trap(
        spec=spec,
        current_configs=configs,
        existing_invariants=spec.baseline_invariants,
        step_idx=1,
        sequence_id="test_shadow",
        split="test",
        rng=rng,
        oracle=oracle,
    )
    assert res is not None, "Failed to generate shadowing trap"
    assert res.trap_kind == "shadowing"
    assert res.broken_invariant_id

    # Proof check: Naive edit MUST break the target invariant
    naive_cfgs = dict(configs)
    naive_cfgs.update(res.naive_edit)
    naive_check = oracle.check(naive_cfgs, spec.baseline_invariants)
    assert not naive_check[res.broken_invariant_id], "Naive edit was supposed to break earlier invariant!"

    # Proof check: Gold edit MUST pass all invariants (baseline + new)
    gold_cfgs = dict(configs)
    gold_cfgs.update(res.gold_patch)
    gold_check = oracle.check(gold_cfgs, spec.baseline_invariants + [res.invariant])
    assert all(gold_check.values()), "Gold edit must pass all invariants!"


def test_prefix_overlap_trap_automated_proof():
    """Verify prefix overlap trap naive edit provably breaks an earlier reach invariant, while gold edit passes."""
    spec = get_topology("topo_test_small")
    configs = generate_topology_configs(spec)
    oracle = ReferenceOracle()
    rng = random.Random(42)

    res = build_prefix_overlap_trap(
        spec=spec,
        current_configs=configs,
        existing_invariants=spec.baseline_invariants,
        step_idx=1,
        sequence_id="test_prefix_overlap",
        split="test",
        rng=rng,
        oracle=oracle,
    )
    assert res is not None, "Failed to generate prefix overlap trap"
    assert res.trap_kind == "prefix_overlap"
    assert res.broken_invariant_id

    # Proof check: Naive edit breaks earlier reach invariant
    naive_cfgs = dict(configs)
    naive_cfgs.update(res.naive_edit)
    naive_check = oracle.check(naive_cfgs, spec.baseline_invariants)
    assert not naive_check[res.broken_invariant_id], "Naive edit was supposed to break reach invariant!"

    # Proof check: Gold edit passes all
    gold_cfgs = dict(configs)
    gold_cfgs.update(res.gold_patch)
    gold_check = oracle.check(gold_cfgs, spec.baseline_invariants + [res.invariant])
    assert all(gold_check.values()), "Gold edit must pass all invariants!"


def test_isolation_leak_trap_automated_proof():
    """Verify isolation leak trap naive edit provably breaks VRF isolation, while gold edit passes."""
    spec = get_topology("topo_test_small")
    configs = generate_topology_configs(spec)
    oracle = ReferenceOracle()
    rng = random.Random(42)

    res = build_isolation_leak_trap(
        spec=spec,
        current_configs=configs,
        existing_invariants=spec.baseline_invariants,
        step_idx=1,
        sequence_id="test_iso_leak",
        split="test",
        rng=rng,
        oracle=oracle,
    )
    assert res is not None, "Failed to generate isolation leak trap"
    assert res.trap_kind == "isolation_leak"
    assert res.broken_invariant_id

    # Proof check: Naive edit leaks isolation
    naive_cfgs = dict(configs)
    naive_cfgs.update(res.naive_edit)
    naive_check = oracle.check(naive_cfgs, spec.baseline_invariants)
    assert not naive_check[res.broken_invariant_id], "Naive edit was supposed to leak VRF isolation!"

    # Proof check: Gold edit passes all
    gold_cfgs = dict(configs)
    gold_cfgs.update(res.gold_patch)
    gold_check = oracle.check(gold_cfgs, spec.baseline_invariants + [res.invariant])
    assert all(gold_check.values()), "Gold edit must pass all invariants!"


def test_unified_trap_catalog_coordinator():
    """Verify generate_trap_step coordinator successfully produces proved traps."""
    spec = get_topology("topo_test_small")
    configs = generate_topology_configs(spec)
    oracle = ReferenceOracle()
    rng = random.Random(123)

    trap = generate_trap_step(
        spec=spec,
        current_configs=configs,
        existing_invariants=spec.baseline_invariants,
        step_idx=1,
        sequence_id="test_coord",
        split="test",
        rng=rng,
        oracle=oracle,
    )
    assert trap is not None
    assert trap.trap_kind in ("shadowing", "prefix_overlap", "isolation_leak")
    assert trap.broken_invariant_id
    assert trap.naive_description
