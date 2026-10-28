"""Tests for intent templates, paraphrasing, disjointness, and gold patch formatting."""

import pytest
from agent.contract.models import IntentType, InvariantKind
from agent.prompts.parser import parse_model_output
from agent.prompts.resolve import resolve_devices
from generator.intents.paraphrases import (
    PARAPHRASE_POOLS,
    TEST_RESERVED_PHRASES,
    get_paraphrases,
)
from generator.intents.templates import (
    format_gold_patch_as_model_output,
    generate_acl_deny_step,
    generate_acl_permit_step,
    generate_prefix_filter_step,
    generate_vrf_isolate_step,
    generate_waypoint_step,
)
from generator.topology.catalog import get_topology
from generator.topology.generator import generate_topology_configs
import random


def test_intent_types_coverage():
    """Verify that all 5 contract intent types are covered."""
    covered = {
        IntentType.ACL_PERMIT,
        IntentType.ACL_DENY,
        IntentType.VRF_ISOLATE,
        IntentType.PREFIX_FILTER,
        IntentType.WAYPOINT,
    }
    assert set(PARAPHRASE_POOLS.keys()) == covered


def test_paraphrase_pools_disjoint():
    """Verify that train and test paraphrase pools are strictly disjoint for all intent types."""
    for itype in IntentType:
        train_pool = set(get_paraphrases(itype, split="train"))
        test_pool = set(get_paraphrases(itype, split="test"))
        assert len(train_pool) >= 4, f"Train pool for {itype} too small"
        assert len(test_pool) >= 2, f"Test pool for {itype} too small"
        assert train_pool.isdisjoint(test_pool), f"Overlap found in paraphrase pools for {itype}!"


def test_test_reserved_phrases_not_in_train():
    """Verify reserved test phrasings are never present in train pool."""
    for itype, reserved_list in TEST_RESERVED_PHRASES.items():
        train_pool = get_paraphrases(itype, split="train")
        for template in train_pool:
            for phrase in reserved_list:
                assert phrase.lower() not in template.lower(), (
                    f"Reserved test phrase '{phrase}' leaked into train template '{template}'!"
                )


def test_intent_generation_roundtrip():
    """Verify all 5 generators create valid intents and gold patches that round-trip cleanly."""
    spec = get_topology("topo_test_small")
    configs = generate_topology_configs(spec)
    rng = random.Random(42)

    generators = [
        generate_acl_permit_step,
        generate_acl_deny_step,
        generate_vrf_isolate_step,
        generate_prefix_filter_step,
        generate_waypoint_step,
    ]

    for gen_fn in generators:
        intent, inv, patch = gen_fn(
            spec=spec,
            current_configs=configs,
            step_idx=1,
            sequence_id="test_seq",
            split="test",
            rng=rng,
        )
        assert intent.text
        assert intent.gold_patch
        assert inv.id
        assert patch

        # Format gold patch as markdown model output
        fenced_md = format_gold_patch_as_model_output(intent.gold_patch)
        parsed = parse_model_output(fenced_md)
        assert len(parsed.devices) == len(intent.gold_patch)
        assert parsed.is_ok

        resolved = resolve_devices(parsed.devices, spec.inventory)
        assert resolved.is_ok
        assert len(resolved.errors) == 0
        assert len(resolved.warnings) == 0
        assert set(resolved.devices.keys()) == set(intent.gold_patch.keys())
        for dev, cfg in resolved.devices.items():
            assert cfg == intent.gold_patch[dev].strip()
