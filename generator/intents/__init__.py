"""Intents package for generating verified intent steps, invariants, and gold patches."""

from generator.intents.paraphrases import (
    TEST_PARAPHRASES,
    TRAIN_PARAPHRASES,
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

__all__ = [
    "TRAIN_PARAPHRASES",
    "TEST_PARAPHRASES",
    "get_paraphrases",
    "format_gold_patch_as_model_output",
    "generate_acl_permit_step",
    "generate_acl_deny_step",
    "generate_vrf_isolate_step",
    "generate_prefix_filter_step",
    "generate_waypoint_step",
]
