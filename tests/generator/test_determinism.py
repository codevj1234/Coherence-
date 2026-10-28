"""Tests for byte-for-byte determinism across runs, seeds, and PYTHONHASHSEED environments."""

import os
from pathlib import Path
import subprocess
import sys
import pytest
from generator.sequences.builder import build_sequence, save_sequence_files
from generator.sequences.sidecar import compute_file_sha256
from generator.topology.catalog import get_topology


def test_seed_determinism_same_process(tmp_path):
    """Verify that identical seed produces byte-identical files in the same process."""
    spec = get_topology("topo_test_small")
    dir1 = tmp_path / "run1"
    dir2 = tmp_path / "run2"

    seq1 = build_sequence(spec=spec, length=10, seed=4242, split="test")
    p1, m1, s1 = save_sequence_files(seq1, dir1)

    seq2 = build_sequence(spec=spec, length=10, seed=4242, split="test")
    p2, m2, s2 = save_sequence_files(seq2, dir2)

    sha1 = compute_file_sha256(p1)
    sha2 = compute_file_sha256(p2)
    assert sha1 == sha2, "JSONL files differ for identical seed!"
    assert p1.read_bytes() == p2.read_bytes()

    # Manifest and prompt-safe files must also match byte for byte
    assert m1.read_bytes() == m2.read_bytes()
    assert s1.read_bytes() == s2.read_bytes()


def test_different_seeds_produce_different_sequences(tmp_path):
    """Verify that different seeds produce different sequences."""
    spec = get_topology("topo_test_small")
    dir1 = tmp_path / "run1"
    dir2 = tmp_path / "run2"

    seq1 = build_sequence(spec=spec, length=10, seed=1001, split="test")
    p1, _, _ = save_sequence_files(seq1, dir1)

    seq2 = build_sequence(spec=spec, length=10, seed=1002, split="test")
    p2, _, _ = save_sequence_files(seq2, dir2)

    assert compute_file_sha256(p1) != compute_file_sha256(p2)


def test_pythonhashseed_determinism_across_subprocesses(tmp_path):
    """Verify byte-for-byte determinism across different PYTHONHASHSEED values via subprocess."""
    out_dir_1 = tmp_path / "hashseed_0"
    out_dir_2 = tmp_path / "hashseed_42"

    cmd_template = [
        sys.executable,
        "-m",
        "generator.build",
        "--topology",
        "topo_test_small",
        "--length",
        "10",
        "--seed",
        "8888",
        "--split",
        "test",
    ]

    # Run 1: PYTHONHASHSEED=0
    env1 = os.environ.copy()
    env1["PYTHONHASHSEED"] = "0"
    res1 = subprocess.run(
        cmd_template + ["--out", str(out_dir_1)],
        env=env1,
        capture_output=True,
        text=True,
        check=True,
    )
    assert res1.returncode == 0

    # Run 2: PYTHONHASHSEED=42
    env2 = os.environ.copy()
    env2["PYTHONHASHSEED"] = "42"
    res2 = subprocess.run(
        cmd_template + ["--out", str(out_dir_2)],
        env=env2,
        capture_output=True,
        text=True,
        check=True,
    )
    assert res2.returncode == 0

    jsonl_1 = next(out_dir_1.rglob("*.jsonl"))
    jsonl_2 = next(out_dir_2.rglob("*.jsonl"))

    sha1 = compute_file_sha256(jsonl_1)
    sha2 = compute_file_sha256(jsonl_2)
    assert sha1 == sha2, (
        f"Subprocess runs with different PYTHONHASHSEED produced different files!\n"
        f"Hash 1: {sha1}\nHash 2: {sha2}"
    )
    assert jsonl_1.read_bytes() == jsonl_2.read_bytes()
