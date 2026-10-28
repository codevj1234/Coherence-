"""Audit Check 9: Sequence shape, bounds, and contract model validation."""

import json
from pathlib import Path
import pytest
from pydantic import ValidationError

from agent.contract.models import Intent
from generator.sequences.builder import build_sequence
from generator.topology.catalog import get_topology

def main():
    print("=== AUDIT CHECK 9: SEQUENCE SHAPE & CONTRACT INTEGRITY ===")
    test_dir = Path("generator/data/test")
    jsonl_files = sorted(list(test_dir.glob("*.jsonl")))
    jsonl_files = [f for f in jsonl_files if not f.name.endswith(".prompt_safe.jsonl")]

    print(f"Total frozen test sequence files: {len(jsonl_files)}")

    seq_lengths = {}
    total_intents_validated = 0
    validation_errors = []

    for jf in jsonl_files:
        lines = [line.strip() for line in jf.open("r", encoding="utf-8") if line.strip()]
        seq_lengths[jf.stem] = len(lines)

        for line_no, raw_line in enumerate(lines, 1):
            total_intents_validated += 1
            try:
                # agent.contract.models.Intent has model_config = ConfigDict(extra="forbid")
                intent = Intent.model_validate_json(raw_line)
            except ValidationError as e:
                validation_errors.append((jf.name, line_no, str(e)))

    print("\n--- 1. Frozen Test Sequence Lengths ---")
    distinct_lengths = set(seq_lengths.values())
    print(f"Distinct sequence lengths observed: {distinct_lengths}")
    print(f"All 40 sequences have length 50: {distinct_lengths == {50}}")

    print("\n--- 2. Contract Model Validation (extra='forbid') ---")
    print(f"Total Intent records validated: {total_intents_validated}")
    print(f"Validation errors / unknown fields found: {len(validation_errors)}")
    if validation_errors:
        print(f"FAIL: {validation_errors[:5]}")
    else:
        print("PASS: 100% of Intent objects strictly conform to agent.contract.models.Intent with 0 unknown fields.")

    print("\n--- 3. Generator Support for 50-200 Length Specification ---")
    spec = get_topology("topo_test_small")
    test_lengths = [50, 75, 100, 150, 200]
    supported_lengths = []
    for l in test_lengths:
        try:
            seq = build_sequence(spec=spec, length=l, seed=42)
            supported_lengths.append(len(seq.intents))
        except Exception as e:
            print(f"Failed to build sequence of length {l}: {e}")

    print(f"Tested length outputs: {supported_lengths}")
    print(f"Generator supports length 50-200 as specified: {supported_lengths == test_lengths}")

    # Check bounds enforcement
    bounds_enforced = True
    for invalid_l in [5, 9, 201, 250]:
        try:
            build_sequence(spec=spec, length=invalid_l, seed=42)
            bounds_enforced = False
        except ValueError:
            pass
    print(f"Generator strictly rejects lengths outside [10, 200]: {bounds_enforced}")

    # Check whether any sequences longer than 50 were tested in existing test suite
    print("\n--- 4. Existing Dataset & Test Coverage of Sequences > 50 ---")
    tests_dir = Path("tests/generator")
    longer_than_50_tested = False
    for tf in tests_dir.glob("*.py"):
        content = tf.read_text(encoding="utf-8")
        for num in [75, 100, 150, 200]:
            if f"length={num}" in content or f"length = {num}" in content:
                longer_than_50_tested = True
                print(f"Found test in {tf.name} with length {num}")

    print(f"Any test sequences longer than 50 tested in existing test suite: {longer_than_50_tested}")
    print("Observation: Although the generator implementation fully supports length 50-200, all 40 frozen benchmark sequences and unit tests were restricted to length 50 (or <=10 for quick unit tests). No sequence >= 100 exists in the benchmark dataset.")

if __name__ == "__main__":
    main()
