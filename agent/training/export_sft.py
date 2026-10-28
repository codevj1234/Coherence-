"""Export instruction-tuning SFT dataset from verified training sequences.

Converts TRAIN benchmark sequences only into instruction-tuning JSONL pairs.
Prompts are constructed with agent.prompts.builder, and targets are rendered
as model output code-blocks using format_gold_patch_as_model_output.
Enforces a strict zero-leakage check against all test sequence IDs, test topologies,
and test-reserved intent paraphrases.
"""

import argparse
import json
from pathlib import Path
import sys
from typing import Any

from agent.contract.io import read_jsonl
from agent.contract.models import Intent
from agent.prompts.builder import build_prompt
from generator.intents.paraphrases import TEST_PARAPHRASES, TEST_RESERVED_PHRASES
from generator.intents.templates import format_gold_patch_as_model_output
from generator.topology.catalog import get_topology
from generator.topology.generator import generate_topology_configs


class DataLeakageError(Exception):
    """Raised when test benchmark identifiers, topologies, or phrases appear in training data."""


def check_leakage(
    text: str,
    test_seq_ids: set[str],
    test_topologies: set[str],
    test_phrases: set[str],
) -> None:
    """Validate that text does not contain any test sequence IDs, topologies, or reserved phrases.

    Args:
        text: Prompt or completion string to inspect.
        test_seq_ids: Set of test sequence IDs.
        test_topologies: Set of test topology IDs.
        test_phrases: Set of reserved test phrases / n-grams.

    Raises:
        DataLeakageError: If any prohibited test token is found.
    """
    text_lower = text.lower()

    for seq_id in test_seq_ids:
        if seq_id.lower() in text_lower:
            raise DataLeakageError(f"Contamination error: test sequence ID '{seq_id}' leaked into training text.")

    for topo in test_topologies:
        if topo.lower() in text_lower:
            raise DataLeakageError(f"Contamination error: test topology '{topo}' leaked into training text.")

    for phrase in test_phrases:
        if phrase.lower() in text_lower:
            raise DataLeakageError(f"Contamination error: test reserved phrase '{phrase}' leaked into training text.")


def export_sft_dataset(
    train_dir: str = "generator/data/train",
    data_dir: str = "generator/data",
    output_path: str = "generator/data/sft_train.jsonl",
    condition: str = "C0",
) -> tuple[int, int]:
    """Export instruction-tuning dataset from train sequences.

    Args:
        train_dir: Path to directory containing train sequence files.
        data_dir: Path to generator/data (must contain FROZEN.json).
        output_path: Destination JSONL output path.
        condition: Prompt builder condition ('C0').

    Returns:
        Tuple of (total_sequences, total_examples).

    Raises:
        FileNotFoundError: If train_dir or FROZEN.json is missing.
        DataLeakageError: If test data leakage is detected.
    """
    train_path = Path(train_dir)
    data_path = Path(data_dir)
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    frozen_file = data_path / "FROZEN.json"
    if not frozen_file.is_file():
        raise FileNotFoundError(f"Cannot find FROZEN.json at '{frozen_file}' for leakage validation.")

    with frozen_file.open("r", encoding="utf-8") as f:
        frozen_manifest = json.load(f)

    test_seq_ids = set(frozen_manifest.keys())
    test_topologies = {"topo_test_large", "topo_test_small"}
    test_phrases = set()
    for phrase_list in TEST_RESERVED_PHRASES.values():
        test_phrases.update(phrase_list)

    # Locate all raw train sequence JSONL files (excluding .prompt_safe.jsonl)
    train_files = sorted([
        f for f in train_path.glob("*.jsonl")
        if not f.name.endswith(".prompt_safe.jsonl")
    ])

    if not train_files:
        raise FileNotFoundError(f"No train sequence JSONL files found in '{train_path}'.")

    total_examples = 0
    total_prompt_chars = 0
    total_target_chars = 0

    with out_file.open("w", encoding="utf-8") as out_f:
        for seq_file in train_files:
            manifest_file = seq_file.with_name(seq_file.name.replace(".jsonl", ".manifest.json"))
            if not manifest_file.is_file():
                raise FileNotFoundError(f"Missing manifest for train sequence '{seq_file.name}'.")

            with manifest_file.open("r", encoding="utf-8") as mf:
                manifest_data = json.load(mf)

            topo_id = manifest_data["topology_id"]
            if topo_id in test_topologies:
                raise DataLeakageError(f"Sequence {seq_file.name} references test topology {topo_id}!")

            spec = get_topology(topo_id)
            current_configs = generate_topology_configs(spec)

            intents = list(read_jsonl(seq_file, Intent))
            for intent in intents:
                if not intent.gold_patch:
                    raise ValueError(f"Intent {intent.id} in {seq_file.name} lacks gold_patch!")

                prompt_obj = build_prompt(
                    device_configs=current_configs,
                    intent_text=intent.text,
                    condition=condition,
                )
                prompt_text = f"{prompt_obj.system}\n\n{prompt_obj.user}"
                target_text = format_gold_patch_as_model_output(intent.gold_patch)

                # Strict test leakage check on both prompt and target
                check_leakage(prompt_text, test_seq_ids, test_topologies, test_phrases)
                check_leakage(target_text, test_seq_ids, test_topologies, test_phrases)

                sft_record = {
                    "sequence_id": intent.sequence_id,
                    "step": intent.step,
                    "prompt": prompt_text,
                    "response": target_text,
                    "messages": [
                        {"role": "system", "content": prompt_obj.system},
                        {"role": "user", "content": prompt_obj.user},
                        {"role": "assistant", "content": target_text},
                    ],
                }

                out_f.write(json.dumps(sft_record) + "\n")

                # Advance configs by applying gold patch for next step
                for dev_name, new_cfg in intent.gold_patch.items():
                    current_configs[dev_name] = new_cfg

                total_examples += 1
                total_prompt_chars += len(prompt_text)
                total_target_chars += len(target_text)

    print("=== SFT Export Completed Successfully ===")
    print(f"Total Sequences Processed: {len(train_files)}")
    print(f"Total SFT Examples Exported: {total_examples}")
    print(f"Approx Total Prompt Tokens (~4 chars/tok): {total_prompt_chars // 4:,}")
    print(f"Approx Total Target Tokens (~4 chars/tok): {total_target_chars // 4:,}")
    print(f"Test Leakage Status: VERIFIED CLEAN (0 test IDs, 0 test topos, 0 reserved phrases)")
    print(f"Output File: {output_path}")

    return len(train_files), total_examples


def main() -> None:
    """CLI entrypoint for SFT export."""
    parser = argparse.ArgumentParser(
        description="Export verified instruction-tuning SFT dataset from train sequences."
    )
    parser.add_argument(
        "--train-dir",
        default="generator/data/train",
        help="Path to directory containing train sequences (default: generator/data/train).",
    )
    parser.add_argument(
        "--data-dir",
        default="generator/data",
        help="Path to generator/data containing FROZEN.json (default: generator/data).",
    )
    parser.add_argument(
        "--out",
        default="generator/data/sft_train.jsonl",
        help="Destination JSONL output path (default: generator/data/sft_train.jsonl).",
    )
    parser.add_argument(
        "--condition",
        default="C0",
        choices=["C0"],
        help="Prompt condition (default: C0).",
    )

    args = parser.parse_args()
    try:
        export_sft_dataset(
            train_dir=args.train_dir,
            data_dir=args.data_dir,
            output_path=args.out,
            condition=args.condition,
        )
    except Exception as err:
        print(f"Error during SFT export: {err}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
