"""Deterministic sequence generator building benchmark sequences with verified gold patches and traps."""

from dataclasses import dataclass
import json
from pathlib import Path
import random
from typing import Any, Literal
from agent.contract.models import Intent, Invariant
from generator import __version__
from generator.intents.templates import (
    format_gold_patch_as_model_output,
    generate_acl_deny_step,
    generate_acl_permit_step,
    generate_prefix_filter_step,
    generate_vrf_isolate_step,
    generate_waypoint_step,
)
from generator.oracle.reference import ReferenceOracle
from generator.sequences.contradictions import is_contradictory
from generator.sequences.sidecar import SequenceManifest, compute_file_sha256
from generator.topology.generator import generate_topology_configs
from generator.topology.spec import TopologySpec
from generator.traps.catalog import generate_trap_step


@dataclass
class GeneratedSequence:
    """In-memory representation of a generated benchmark sequence."""

    sequence_id: str
    topology_id: str
    split: str
    seed: int
    intents: list[Intent]
    invariants: list[Invariant]
    manifest: SequenceManifest


def build_sequence(
    spec: TopologySpec,
    length: int = 50,
    seed: int = 42,
    sequence_id: str | None = None,
    split: Literal["train", "test"] | None = None,
) -> GeneratedSequence:
    """Build a complete, verified sequence of configuration change steps.

    Guarantees:
    - Deterministic execution using exclusively `random.Random(seed)`.
    - Trap ratio between 25% and 35%, distributed throughout sequence.
    - Every step's gold patch verified against the cumulative invariant ledger.
    - Zero semantic contradictions with earlier invariants.
    """
    if length < 10 or length > 200:
        raise ValueError(f"Sequence length must be between 10 and 200, got {length}")

    active_split = split if split is not None else spec.split
    seq_id = sequence_id or f"seq_{spec.id}_{active_split}_s{seed}"

    # Pure seeded RNG
    rng = random.Random(seed)
    oracle = ReferenceOracle()

    # Initial state
    current_configs = generate_topology_configs(spec)
    cumulative_invariants = list(spec.baseline_invariants)

    # Determine target number of traps (about 30%)
    import math
    target_traps = int(round(length * 0.30))
    min_traps = int(math.ceil(length * 0.25))
    max_traps = int(math.floor(length * 0.35))

    # Select steps where traps will be attempted (start from step 2 so baseline rules exist)
    eligible_steps = list(range(2, length))
    k_candidates = min(len(eligible_steps), max(int(target_traps * 2.2), int(length * 0.70)))
    trap_candidates = set(rng.sample(eligible_steps, k=k_candidates))

    intents: list[Intent] = []
    traps_manifest: dict[str, dict[str, Any]] = {}
    traps_by_kind: dict[str, int] = {"shadowing": 0, "prefix_overlap": 0, "isolation_leak": 0}
    invariants_added: dict[str, dict[str, Any]] = {}

    regular_builders = [
        generate_acl_permit_step,
        generate_acl_deny_step,
        generate_vrf_isolate_step,
        generate_prefix_filter_step,
        generate_waypoint_step,
    ]

    for step_idx in range(length):
        step_success = False
        remaining_steps = length - step_idx
        needed_for_min = min_traps - len(traps_manifest)

        min_per_kind = int(math.ceil(target_traps * 0.20))
        has_deficit = any(traps_by_kind[k] < min_per_kind for k in traps_by_kind)

        is_trap_step = (
            step_idx >= 2
            and len(traps_manifest) < max_traps
            and (
                (len(traps_manifest) < target_traps and step_idx in trap_candidates)
                or (has_deficit and step_idx in trap_candidates)
                or (remaining_steps <= needed_for_min)
                or (has_deficit and remaining_steps <= min_per_kind * 3)
            )
        )

        # Attempt trap if scheduled
        if is_trap_step:
            candidate_kinds = sorted(traps_by_kind.keys(), key=lambda k: traps_by_kind[k])
            preferred_kind = candidate_kinds[0]
            # Strictly enforce generating the deficit kind if it has not reached 20% floor
            is_strict = traps_by_kind[preferred_kind] < min_per_kind

            trap_res = generate_trap_step(
                spec=spec,
                current_configs=current_configs,
                existing_invariants=cumulative_invariants,
                step_idx=step_idx,
                sequence_id=seq_id,
                split=active_split,
                rng=rng,
                oracle=oracle,
                preferred_kind=preferred_kind,
                strict_kind=is_strict,
            )
            if trap_res is not None:
                # Contradiction check
                if not is_contradictory(trap_res.invariant, cumulative_invariants):
                    # Verify gold edit passes all cumulative invariants
                    candidate_cfgs = dict(current_configs)
                    candidate_cfgs.update(trap_res.gold_patch)
                    check_res = oracle.check(candidate_cfgs, cumulative_invariants + [trap_res.invariant])
                    if all(check_res.values()):
                        # Commit trap step
                        current_configs = candidate_cfgs
                        cumulative_invariants.append(trap_res.invariant)
                        intents.append(trap_res.intent)
                        invariants_added[str(step_idx)] = trap_res.invariant.model_dump()
                        traps_manifest[str(step_idx)] = {
                            "step": step_idx,
                            "kind": trap_res.trap_kind,
                            "broken_invariant_id": trap_res.broken_invariant_id,
                            "naive_description": trap_res.naive_description,
                        }
                        traps_by_kind[trap_res.trap_kind] += 1
                        step_success = True

        # If not a trap or trap generation failed, emit standard intent
        if not step_success:
            max_attempts = 30
            for _ in range(max_attempts):
                builder_fn = rng.choice(regular_builders)
                intent_cand, inv_cand, patch_cand = builder_fn(
                    spec=spec,
                    current_configs=current_configs,
                    step_idx=step_idx,
                    sequence_id=seq_id,
                    split=active_split,
                    rng=rng,
                )

                if is_contradictory(inv_cand, cumulative_invariants):
                    continue

                candidate_cfgs = dict(current_configs)
                candidate_cfgs.update(patch_cand)
                check_res = oracle.check(candidate_cfgs, cumulative_invariants + [inv_cand])
                if all(check_res.values()):
                    # Commit regular step
                    current_configs = candidate_cfgs
                    cumulative_invariants.append(inv_cand)
                    intents.append(intent_cand)
                    invariants_added[str(step_idx)] = inv_cand.model_dump()
                    step_success = True
                    break

            if not step_success:
                raise RuntimeError(
                    f"Failed to generate valid verified intent for step {step_idx} "
                    f"after {max_attempts} attempts on topology {spec.id}."
                )

    trap_count = len(traps_manifest)
    trap_ratio = trap_count / length
    if not (0.25 <= trap_ratio <= 0.35):
        raise AssertionError(
            f"Sequence {seq_id} achieved trap ratio {trap_ratio:.3f} ({trap_count}/{length}), "
            f"outside required [0.25, 0.35] bounds."
        )

    # Enforce >= 20% trap mix per kind
    min_per_kind = int(math.ceil(trap_count * 0.20))
    for k in ("shadowing", "prefix_overlap", "isolation_leak"):
        count = traps_by_kind[k]
        if count < min_per_kind or count == 0:
            raise AssertionError(
                f"Sequence {seq_id} failed trap mix quota: kind '{k}' has {count}/{trap_count} traps "
                f"({count / trap_count:.1%}), below required 20% floor ({min_per_kind} traps)."
            )

    manifest = SequenceManifest(
        sequence_id=seq_id,
        topology_id=spec.id,
        split=active_split,
        seed=seed,
        generator_version=__version__,
        length=length,
        trap_count=trap_count,
        trap_ratio=round(trap_ratio, 4),
        traps=traps_manifest,
        invariants_added=invariants_added,
        jsonl_sha256="",  # Will be populated on save
    )

    return GeneratedSequence(
        sequence_id=seq_id,
        topology_id=spec.id,
        split=active_split,
        seed=seed,
        intents=intents,
        invariants=cumulative_invariants,
        manifest=manifest,
    )


def save_sequence_files(seq: GeneratedSequence, output_dir: str | Path) -> tuple[Path, Path, Path]:
    """Save sequence JSONL, sidecar manifest, and prompt-safe view to disk.

    Returns:
        Tuple of (jsonl_path, manifest_path, prompt_safe_path)
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    jsonl_path = out_dir / f"{seq.sequence_id}.jsonl"
    manifest_path = out_dir / f"{seq.sequence_id}.manifest.json"
    prompt_safe_path = out_dir / f"{seq.sequence_id}.prompt_safe.jsonl"

    # 1. Write full Intent JSONL
    with jsonl_path.open("w", encoding="utf-8") as f_jsonl:
        for intent in seq.intents:
            # Pydantic serialization with sort_keys
            line = json.dumps(intent.model_dump(), sort_keys=True)
            f_jsonl.write(line + "\n")

    # 2. Compute JSONL SHA-256 and write sidecar manifest
    seq.manifest.jsonl_sha256 = compute_file_sha256(jsonl_path)
    seq.manifest.write_file(manifest_path)

    # 3. Write prompt-safe JSONL (gold_patch stripped)
    with prompt_safe_path.open("w", encoding="utf-8") as f_safe:
        for intent in seq.intents:
            safe_data = intent.model_dump()
            safe_data["gold_patch"] = None
            line = json.dumps(safe_data, sort_keys=True)
            f_safe.write(line + "\n")

    return jsonl_path, manifest_path, prompt_safe_path
