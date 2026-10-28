"""Automated tests for replay execution, corruption detection, and frozen dataset verification."""

from pathlib import Path
import pytest
from generator.replay_check import (
    replay_single_sequence,
    run_agent_pipeline_check,
    run_corruption_suite,
)
from generator.verify_frozen import verify_frozen_dataset


def test_frozen_dataset_verification():
    """Verify all 40 frozen benchmark test sequences match their recorded SHA-256 hashes."""
    frozen_manifest_path = Path("generator/data/FROZEN.json")
    if not frozen_manifest_path.is_file():
        pytest.skip("FROZEN.json not found; build bulk dataset first.")

    ok = verify_frozen_dataset(frozen_manifest_path)
    assert ok is True, f"Frozen dataset verification failed for: {frozen_manifest_path}"


def test_replay_zero_regressions_sample():
    """Verify that replaying a sample of frozen sequences yields 0 regressions."""
    data_dir = Path("generator/data/test")
    if not data_dir.is_dir():
        pytest.skip("generator/data/test directory not found.")

    sample_files = sorted(list(data_dir.glob("*.jsonl")))[:2]
    for jf in sample_files:
        if jf.name.endswith(".prompt_safe.jsonl"):
            continue
        mf = jf.with_name(jf.stem + ".manifest.json")
        total_steps, regressions = replay_single_sequence(jf, mf)
        assert total_steps > 0
        assert regressions == 0, f"Replay of {jf} encountered {regressions} regressions!"


def test_corruption_suite_100_percent_detection():
    """Verify that intentional corruption suite achieves 100% detection on sample."""
    data_dir = Path("generator/data/test")
    if not data_dir.is_dir():
        pytest.skip("generator/data/test directory not found.")

    num_steps, detected = run_corruption_suite(data_dir, num_corruptions=20, seed=12345)
    assert detected == num_steps, f"Expected 100% detection, got {detected}/{num_steps}"


def test_agent_pipeline_check():
    """Verify prompt-safe intents successfully pass through agent prompt builder and mock client."""
    safe_files = list(Path("generator/data/test").glob("*.prompt_safe.jsonl"))
    if not safe_files:
        pytest.skip("No prompt-safe files found in generator/data/test.")

    ok = run_agent_pipeline_check(safe_files[0])
    assert ok is True


def test_replay_catches_bad_gold_patch_at_correct_step(tmp_path):
    """Verify that a deliberately corrupted gold patch is caught at the exact step index during replay."""
    from generator.sequences.builder import build_sequence, save_sequence_files
    from generator.topology.catalog import get_topology

    spec = get_topology("topo_test_small")
    seq = build_sequence(spec=spec, length=10, seed=123, split="test")

    # Deliberately corrupt step 4's gold patch
    corrupted_step = 4
    orig_patch = seq.intents[corrupted_step].gold_patch
    target_dev = list(orig_patch.keys())[0]
    bad_cfg = orig_patch[target_dev].replace(
        "ip access-list extended EDGE_IN\n",
        "ip access-list extended EDGE_IN\n deny ip any any\n",
    )
    seq.intents[corrupted_step].gold_patch = {target_dev: bad_cfg}

    jsonl_p, manifest_p, _ = save_sequence_files(seq, tmp_path)

    res = replay_single_sequence(jsonl_p, manifest_p)
    assert res.regressions > 0, "Expected replay to flag regressions from corrupted gold patch"
    assert corrupted_step in res.regressions_by_step, f"Expected regression at step {corrupted_step}, got: {list(res.regressions_by_step.keys())}"

