"""Tests for sequence builder, trap ratio enforcement, contradiction checker, and prompt-safe files."""

import pytest
from agent.contract.models import Intent, IntentType, Invariant, InvariantKind
from agent.prompts.builder import build_prompt
from generator.sequences.builder import build_sequence, save_sequence_files
from generator.sequences.contradictions import is_contradictory
from generator.topology.catalog import get_topology


def test_contradiction_checker():
    """Verify semantic contradiction detection between new and existing invariants."""
    inv_existing = Invariant(
        id="inv_reach_1",
        kind=InvariantKind.REACH,
        source="10.10.1.10",
        destination="10.10.6.10",
        headers={"protocol": "tcp", "dst_port": 80},
        created_at_step=0,
    )

    # 1. Exact contradiction: isolate the exact same flow
    inv_contradict = Invariant(
        id="inv_iso_1",
        kind=InvariantKind.ISOLATE,
        source="10.10.1.10",
        destination="10.10.6.10",
        headers={"protocol": "tcp", "dst_port": 80},
        created_at_step=0,
    )
    assert is_contradictory(inv_contradict, [inv_existing]) is True

    # 2. Non-contradiction: isolate different port
    inv_different_port = Invariant(
        id="inv_iso_2",
        kind=InvariantKind.ISOLATE,
        source="10.10.1.10",
        destination="10.10.6.10",
        headers={"protocol": "tcp", "dst_port": 443},
        created_at_step=0,
    )
    assert is_contradictory(inv_different_port, [inv_existing]) is False


def test_sequence_builder_bounds_and_properties():
    """Verify sequence length, trap ratio bounds [25%, 35%], and manifest sidecar."""
    spec = get_topology("topo_test_small")
    seq = build_sequence(spec=spec, length=50, seed=777, split="test")

    assert len(seq.intents) == 50
    trap_count = len(seq.manifest.traps)
    trap_ratio = trap_count / 50.0
    assert 0.25 <= trap_ratio <= 0.35, f"Trap ratio {trap_ratio} outside [0.25, 0.35]"

    assert seq.manifest.sequence_id == seq.sequence_id
    assert seq.manifest.topology_id == spec.id
    assert seq.manifest.split == "test"
    assert seq.manifest.length == 50
    assert len(seq.manifest.invariants_added) == 50


def test_prompt_safe_builder_acceptance_and_gold_patch_rejection(tmp_path):
    """Verify prompt-safe views have gold_patch stripped and build_prompt rejects gold_patch."""
    spec = get_topology("topo_test_small")
    seq = build_sequence(spec=spec, length=10, seed=123, split="test")

    jsonl_p, manifest_p, safe_p = save_sequence_files(seq, tmp_path)

    # 1. build_prompt MUST accept prompt-safe intent (gold_patch is None)
    safe_intent = Intent.model_validate_json(safe_p.read_text().splitlines()[0])
    assert safe_intent.gold_patch is None

    prompt = build_prompt(
        device_configs={"fw1": "hostname fw1\n!"},
        intent_text=safe_intent.text,
        condition="C0",
    )
    assert safe_intent.text in prompt.user

    # 2. build_prompt MUST reject raw intent carrying gold_patch
    raw_intent = seq.intents[0]
    assert raw_intent.gold_patch is not None
    with pytest.raises(TypeError, match="gold_patch"):
        build_prompt(
            device_configs={"fw1": "hostname fw1\n!"},
            intent_text=raw_intent,
            condition="C0",
        )


def test_trap_mix_enforces_twenty_percent_per_kind():
    """Verify each of the 3 trap kinds constitutes at least 20% of total traps and none is zero."""
    spec = get_topology("topo_test_small")
    seq = build_sequence(spec=spec, length=50, seed=777, split="test")

    traps = seq.manifest.traps
    total_traps = len(traps)
    assert total_traps > 0

    kinds = {"shadowing": 0, "prefix_overlap": 0, "isolation_leak": 0}
    for trap_info in traps.values():
        k = trap_info["kind"]
        assert k in kinds, f"Unexpected trap kind: {k}"
        kinds[k] += 1

    for kind, count in kinds.items():
        assert count > 0, f"Trap kind '{kind}' has 0 traps in sequence {seq.sequence_id}"
        ratio = count / total_traps
        assert ratio >= 0.20, f"Trap kind '{kind}' ratio {ratio:.1%} is below the 20% requirement ({count}/{total_traps})"

