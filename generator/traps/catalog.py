"""Unified trap catalog coordinator for generating provably regressive trap steps."""

import random
from typing import Literal
from agent.contract.models import Invariant
from generator.oracle.reference import ReferenceOracle
from generator.topology.spec import TopologySpec
from generator.traps.isolation_leak import build_isolation_leak_trap
from generator.traps.prefix_overlap import build_prefix_overlap_trap
from generator.traps.shadowing import TrapResult, build_shadowing_trap


def generate_trap_step(
    spec: TopologySpec,
    current_configs: dict[str, str],
    existing_invariants: list[Invariant],
    step_idx: int,
    sequence_id: str,
    split: Literal["train", "test"],
    rng: random.Random,
    oracle: ReferenceOracle,
    preferred_kind: str | None = None,
    strict_kind: bool = False,
) -> TrapResult | None:
    """Attempt to construct a verified trap step across available trap builders.

    Tries trap kinds in randomized order (or preferred kind first).
    If strict_kind is True, only the preferred_kind builder is evaluated.
    Returns verified TrapResult where naive edit breaks an earlier invariant
    and gold edit passes all invariants, or None if no trap can be proved.
    """
    builders = [
        ("shadowing", build_shadowing_trap),
        ("prefix_overlap", build_prefix_overlap_trap),
        ("isolation_leak", build_isolation_leak_trap),
    ]

    if preferred_kind:
        if strict_kind:
            builders = [b for b in builders if b[0] == preferred_kind]
        else:
            builders.sort(key=lambda x: 0 if x[0] == preferred_kind else 1)
    else:
        rng.shuffle(builders)

    for kind, builder_fn in builders:
        result = builder_fn(
            spec=spec,
            current_configs=current_configs,
            existing_invariants=existing_invariants,
            step_idx=step_idx,
            sequence_id=sequence_id,
            split=split,
            rng=rng,
            oracle=oracle,
        )
        if result is not None:
            return result

    return None
