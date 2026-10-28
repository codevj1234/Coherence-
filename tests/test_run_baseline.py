"""Tests for agent/run_baseline.py using deterministic mock models."""

from pathlib import Path
import tempfile
import pytest

from agent.contract.io import read_jsonl
from agent.contract.models import StepOutcome, StepResult
from agent.run_baseline import run_baseline


def test_baseline_mock_execution(tmp_path: Path):
    """Test standard baseline execution on frozen sequences using mock echo."""
    out_file = tmp_path / "baseline_test.jsonl"
    code = run_baseline(
        model_name="mock",
        config_path="configs/models.yaml",
        data_dir="generator/data",
        max_sequences=1,
        max_steps=3,
        output_path=str(out_file),
    )
    assert code == 0
    assert out_file.is_file()

    results = list(read_jsonl(out_file, StepResult))
    assert len(results) == 3
    for r in results:
        assert r.model == "mock"
        assert r.condition == "C0"
        assert r.parse_ok is True


def test_baseline_mock_malformed(tmp_path: Path):
    """Test baseline execution with malformed mock classifies as PARSE_FAIL."""
    out_file = tmp_path / "baseline_malformed.jsonl"
    code = run_baseline(
        model_name="mock_malformed",
        config_path="configs/models.yaml",
        data_dir="generator/data",
        max_sequences=1,
        max_steps=2,
        output_path=str(out_file),
    )
    assert code == 0
    results = list(read_jsonl(out_file, StepResult))
    assert len(results) == 2
    for r in results:
        assert r.parse_ok is False
        assert r.outcome == StepOutcome.PARSE_FAIL
        assert r.committed is False


def test_baseline_resumption_skips_completed(tmp_path: Path):
    """Test that existing logged steps are skipped when resuming."""
    out_file = tmp_path / "baseline_resume.jsonl"
    # First pass: run 2 steps
    code1 = run_baseline(
        model_name="mock",
        config_path="configs/models.yaml",
        data_dir="generator/data",
        max_sequences=1,
        max_steps=2,
        output_path=str(out_file),
    )
    assert code1 == 0
    results1 = list(read_jsonl(out_file, StepResult))
    assert len(results1) == 2

    # Second pass: resume with max_steps=4
    code2 = run_baseline(
        model_name="mock",
        config_path="configs/models.yaml",
        data_dir="generator/data",
        max_sequences=1,
        max_steps=4,
        output_path=str(out_file),
    )
    assert code2 == 0
    results2 = list(read_jsonl(out_file, StepResult))
    # Should now have 4 steps total without duplicating the first two
    assert len(results2) == 4
