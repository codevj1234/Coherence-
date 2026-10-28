"""Tests for dry run generator, execution pipeline, and summary logging."""

from pathlib import Path
from agent.contract.io import read_jsonl
from agent.contract.models import StepOutcome, StepResult
from agent.dry_run import generate_fake_intents, get_initial_configs, run_dry_run


def test_generate_fake_intents() -> None:
    intents = generate_fake_intents(5, sequence_id="test-seq")
    assert len(intents) == 5
    assert intents[0].id == "dryrun-intent-000"
    assert intents[0].sequence_id == "test-seq"
    assert intents[0].step == 0
    assert intents[4].step == 4


def test_get_initial_configs() -> None:
    configs = get_initial_configs()
    assert "r1" in configs
    assert "r2" in configs
    assert "hostname r1" in configs["r1"]
    assert "hostname r2" in configs["r2"]


def test_run_dry_run_success(tmp_path: Path) -> None:
    out_file = tmp_path / "dry_run.jsonl"
    exit_code = run_dry_run(
        model_name="mock",
        steps=3,
        config_path="configs/models.yaml",
        output_path=str(out_file),
    )
    assert exit_code == 0
    results = list(read_jsonl(out_file, StepResult))
    assert len(results) == 3
    for r in results:
        assert r.outcome == StepOutcome.SUCCESS
        assert r.parse_ok is True
        assert r.committed is True


def test_run_dry_run_malformed(tmp_path: Path) -> None:
    out_file = tmp_path / "malformed.jsonl"
    exit_code = run_dry_run(
        model_name="mock_malformed",
        steps=2,
        config_path="configs/models.yaml",
        output_path=str(out_file),
    )
    assert exit_code == 0
    results = list(read_jsonl(out_file, StepResult))
    assert len(results) == 2
    for r in results:
        assert r.outcome == StepOutcome.PARSE_FAIL
        assert r.parse_ok is False
        assert r.committed is False
