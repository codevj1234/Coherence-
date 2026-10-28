"""PROVISIONAL: Model-in-the-loop baseline runner using pure-Python reference oracle.

Evaluates sequential network configuration changes on frozen benchmark test sequences.
Scoring is performed by the pure-Python reference oracle (PROVISIONAL - not Batfish).
Under Condition C0 (open-loop baseline), proposed edits are retained across steps
regardless of correctness, allowing error drift to accumulate.
"""

import argparse
import json
from pathlib import Path
import sys
import uuid
from typing import Any

from agent.contract.io import read_jsonl
from agent.contract.models import Condition, Intent, Invariant, StepOutcome, StepResult
from agent.llm.client import LLMClient
from agent.llm.config import get_model_config
from agent.logging_.step_log import StepLogger, get_completed_steps
from agent.prompts.builder import build_prompt
from agent.prompts.parser import parse_model_output
from agent.prompts.resolve import resolve_devices
from generator.oracle.reference import ReferenceOracle
from generator.topology.catalog import get_topology
from generator.topology.generator import generate_topology_configs


def run_baseline(
    model_name: str = "mock",
    config_path: str = "configs/models.yaml",
    data_dir: str = "generator/data",
    max_sequences: int = 2,
    max_steps: int = 20,
    output_path: str | None = None,
    condition: str = "C0",
) -> int:
    """Execute baseline evaluation on frozen prompt-safe benchmark sequences.

    PROVISIONAL: Evaluated using reference oracle only.

    Args:
        model_name: Profile name in configs/models.yaml.
        config_path: Path to models.yaml.
        data_dir: Directory containing FROZEN.json and test sequences.
        max_sequences: Maximum number of benchmark sequences to evaluate.
        max_steps: Maximum steps per sequence.
        output_path: JSONL output log path (defaults to runs/baseline_c0_<model>.jsonl).
        condition: Context condition ('C0').

    Returns:
        0 on success, non-zero on error.
    """
    if output_path is None:
        output_path = f"runs/baseline_c0_{model_name}.jsonl"

    print("=== [PROVISIONAL, reference oracle only] Starting C0 Baseline Evaluation ===")
    print(f"Model: {model_name} | Max Sequences: {max_sequences} | Max Steps: {max_steps} | Output: {output_path}")

    try:
        cfg = get_model_config(model_name, config_path)
    except Exception as err:
        print(f"Error loading model configuration '{model_name}': {err}", file=sys.stderr)
        return 1

    data_dir_p = Path(data_dir)
    frozen_manifest = data_dir_p / "FROZEN.json"
    if not frozen_manifest.is_file():
        print(f"Error: Frozen manifest not found at '{frozen_manifest}'", file=sys.stderr)
        return 1

    with frozen_manifest.open("r", encoding="utf-8") as f:
        frozen_data = json.load(f)

    # Sort sequences deterministically
    sorted_seq_ids = sorted(frozen_data.keys())[:max_sequences]
    if not sorted_seq_ids:
        print("Warning: No sequences matched evaluation filter.", file=sys.stderr)
        return 0

    client = LLMClient()
    logger = StepLogger(output_path)
    completed_steps = get_completed_steps(output_path)
    if completed_steps:
        # Resume with the run_id present in the existing log
        run_id = next(iter(completed_steps))[0]
    else:
        run_id = f"baseline-{model_name}-{uuid.uuid4().hex[:8]}"

    oracle = ReferenceOracle()
    total_evaluated_steps = 0
    outcome_counts: dict[StepOutcome, int] = {o: 0 for o in StepOutcome}

    for seq_idx, seq_id in enumerate(sorted_seq_ids, 1):
        entry = frozen_data[seq_id]
        prompt_safe_rel = entry["prompt_safe_file"]
        prompt_safe_path = data_dir_p / prompt_safe_rel

        # Manifest containing invariant definitions
        manifest_path = data_dir_p / prompt_safe_rel.replace(".prompt_safe.jsonl", ".manifest.json")
        if not manifest_path.is_file():
            # Fallback for alternative naming
            manifest_path = data_dir_p / f"test/{seq_id}.manifest.json"

        with manifest_path.open("r", encoding="utf-8") as mf_file:
            seq_manifest = json.load(mf_file)

        topo_id = seq_manifest["topology_id"]
        spec = get_topology(topo_id)

        current_configs = generate_topology_configs(spec)
        cumulative_invariants = list(spec.baseline_invariants)

        intents = list(read_jsonl(prompt_safe_path, Intent))
        steps_to_run = intents[:max_steps]

        print(f"\n--- Sequence [{seq_idx}/{len(sorted_seq_ids)}] {seq_id} ({len(steps_to_run)} steps, topo: {topo_id}) ---")

        for intent in steps_to_run:
            step_key = (run_id, intent.sequence_id, intent.step)
            if step_key in completed_steps:
                print(f"  Step {intent.step} already completed; skipping.")
                continue

            prompt = build_prompt(
                device_configs=current_configs,
                intent_text=intent.text,
                condition=condition,
            )

            gen_res = client.generate(prompt, cfg)
            parsed = parse_model_output(gen_res.text)

            step_inv_data = seq_manifest.get("invariants_added", {}).get(str(intent.step))
            step_inv = Invariant.model_validate(step_inv_data) if step_inv_data else None

            if not parsed.is_ok or len(parsed.errors) > 0:
                outcome = StepOutcome.PARSE_FAIL
                parse_ok = False
                new_intent_ok = False
                committed = False
                broken_invariants = []
                if step_inv is not None:
                    cumulative_invariants.append(step_inv)
            else:
                resolved = resolve_devices(parsed.devices, spec.inventory)
                if not resolved.is_ok or len(resolved.errors) > 0:
                    outcome = StepOutcome.PARSE_FAIL
                    parse_ok = False
                    new_intent_ok = False
                    committed = False
                    broken_invariants = []
                    if step_inv is not None:
                        cumulative_invariants.append(step_inv)
                else:
                    parse_ok = True
                    candidate_configs = dict(current_configs)
                    for dev_name, new_cfg in resolved.devices.items():
                        candidate_configs[dev_name] = new_cfg

                    # Invariant evaluation: past invariants + new step invariant
                    eval_invariants = list(cumulative_invariants)
                    if step_inv is not None:
                        eval_invariants.append(step_inv)

                    check_results = oracle.check(candidate_configs, eval_invariants)

                    if step_inv is not None:
                        new_intent_ok = bool(check_results.get(step_inv.id, False))
                    else:
                        new_intent_ok = True

                    broken_invariants = [
                        inv.id for inv in cumulative_invariants if not check_results.get(inv.id, False)
                    ]
                    has_regression = len(broken_invariants) > 0

                    if new_intent_ok and not has_regression:
                        outcome = StepOutcome.SUCCESS
                    elif not new_intent_ok and not has_regression:
                        outcome = StepOutcome.FORWARD_FAIL
                    elif new_intent_ok and has_regression:
                        outcome = StepOutcome.REGRESSION
                    else:
                        outcome = StepOutcome.BOTH

                    # In C0, keep broken state so errors persist and drift accumulates
                    current_configs = candidate_configs
                    if step_inv is not None:
                        cumulative_invariants.append(step_inv)
                    committed = True

            step_res = StepResult(
                run_id=run_id,
                model=cfg.name,
                condition=Condition(condition),
                sequence_id=intent.sequence_id,
                step=intent.step,
                parse_ok=parse_ok,
                new_intent_ok=new_intent_ok,
                broken_invariants=broken_invariants,
                outcome=outcome,
                retries=0,
                tokens_in=gen_res.tokens_in,
                tokens_out=gen_res.tokens_out,
                tokens_estimated=gen_res.tokens_estimated,
                latency_ms=gen_res.latency_ms,
                committed=committed,
            )

            logger.log(step_res)
            total_evaluated_steps += 1
            outcome_counts[outcome] += 1

            parse_str = "YES" if parse_ok else "NO"
            intent_str = "YES" if new_intent_ok else "NO"
            print(
                f"  Step {intent.step:02d} | Parse: {parse_str} | New Intent: {intent_str} "
                f"| Broken: {len(broken_invariants):02d} | Outcome: {outcome.value:<12} "
                f"| Latency: {gen_res.latency_ms} ms"
            )

    print("\n=== Summary [PROVISIONAL, reference oracle only] ===")
    print(f"Total steps evaluated: {total_evaluated_steps}")
    for o, cnt in outcome_counts.items():
        pct = (cnt / total_evaluated_steps * 100.0) if total_evaluated_steps > 0 else 0.0
        print(f"  {o.value:<14}: {cnt:3d} ({pct:5.1f}%)")
    print(f"Logged to '{output_path}'.\n")

    return 0


def main() -> None:
    """CLI entrypoint for baseline runner."""
    parser = argparse.ArgumentParser(
        description="PROVISIONAL: Run C0 baseline evaluations with reference oracle scoring."
    )
    parser.add_argument(
        "--model",
        default="mock",
        help="Model configuration identifier from configs/models.yaml (default: mock).",
    )
    parser.add_argument(
        "--config",
        default="configs/models.yaml",
        help="Path to models.yaml configuration file (default: configs/models.yaml).",
    )
    parser.add_argument(
        "--data-dir",
        default="generator/data",
        help="Path to benchmark data directory (default: generator/data).",
    )
    parser.add_argument(
        "--max-sequences",
        type=int,
        default=2,
        help="Maximum benchmark sequences to evaluate (default: 2).",
    )
    parser.add_argument(
        "--max-steps",
        type=int,
        default=20,
        help="Maximum steps per sequence (default: 20).",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Destination JSONL output path (default: runs/baseline_c0_<model>.jsonl).",
    )
    parser.add_argument(
        "--condition",
        default="C0",
        choices=["C0"],
        help="Experimental context condition (default: C0).",
    )

    args = parser.parse_args()
    code = run_baseline(
        model_name=args.model,
        config_path=args.config,
        data_dir=args.data_dir,
        max_sequences=args.max_sequences,
        max_steps=args.max_steps,
        output_path=args.out,
        condition=args.condition,
    )
    sys.exit(code)


if __name__ == "__main__":
    main()
