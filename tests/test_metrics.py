"""Unit tests for agent/analysis/metrics.py."""

from pathlib import Path
import pytest

from agent.contract.models import Condition, StepOutcome, StepResult
from agent.analysis.metrics import (
    _compute_half_life,
    _bootstrap_half_life,
    compute_metrics,
    export_csv,
    plot_metrics,
)


def _make_step_result(
    sequence_id: str,
    step: int,
    outcome: StepOutcome,
    broken: list[str] | None = None,
) -> StepResult:
    return StepResult(
        run_id="test-run",
        model="mock",
        condition=Condition.C0,
        sequence_id=sequence_id,
        step=step,
        parse_ok=(outcome != StepOutcome.PARSE_FAIL),
        new_intent_ok=(outcome in (StepOutcome.SUCCESS, StepOutcome.REGRESSION)),
        broken_invariants=broken or [],
        outcome=outcome,
        retries=0,
        tokens_in=100,
        tokens_out=50,
        tokens_estimated=True,
        latency_ms=10,
        committed=True,
    )


def test_half_life_computation():
    # 4 sequences, all regressed at steps 2, 4, 6, 8
    times = [2, 4, 6, 8]
    lengths = [10, 10, 10, 10]
    hl, is_cens = _compute_half_life(times, lengths)
    assert not is_cens
    assert hl == 5.0

    # 4 sequences, only 1 regressed -> censored
    times_cens = [2, None, None, None]
    hl_cens, is_cens2 = _compute_half_life(times_cens, lengths)
    assert is_cens2
    assert str(hl_cens).startswith(">")


def test_bootstrap_half_life():
    times = [2, 3, 4, 5, 6, 7]
    lengths = [10] * 6
    ci_low, ci_high = _bootstrap_half_life(times, lengths, n_resamples=100)
    assert ci_low is not None
    assert ci_high is not None
    assert ci_low <= ci_high


def test_compute_metrics():
    # Sequence 1: 3 steps, SUCCESS -> SUCCESS -> REGRESSION
    seq1 = [
        _make_step_result("seq1", 0, StepOutcome.SUCCESS),
        _make_step_result("seq1", 1, StepOutcome.SUCCESS),
        _make_step_result("seq1", 2, StepOutcome.REGRESSION, broken=["inv1"]),
    ]
    # Sequence 2: 3 steps, SUCCESS -> FORWARD_FAIL -> SUCCESS
    seq2 = [
        _make_step_result("seq2", 0, StepOutcome.SUCCESS),
        _make_step_result("seq2", 1, StepOutcome.FORWARD_FAIL),
        _make_step_result("seq2", 2, StepOutcome.SUCCESS),
    ]

    all_steps = seq1 + seq2
    metrics = compute_metrics(all_steps)

    assert metrics.total_sequences == 2
    assert metrics.total_steps == 6
    assert metrics.outcome_counts[StepOutcome.SUCCESS.value] == 4
    assert metrics.outcome_counts[StepOutcome.FORWARD_FAIL.value] == 1
    assert metrics.outcome_counts[StepOutcome.REGRESSION.value] == 1

    # Per-step success: step 0: 2/2 = 1.0; step 1: 1/2 = 0.5; step 2: 1/2 = 0.5
    assert metrics.per_step_success[0] == 1.0
    assert metrics.per_step_success[1] == 0.5
    assert metrics.per_step_success[2] == 0.5

    # Broken invariants: step 2 has 1 broken
    assert metrics.mean_broken_by_step[2] == 0.5
    assert "PROVISIONAL" in metrics.provisional_label


def test_export_csv_and_plot(tmp_path: Path):
    steps = [
        _make_step_result("seq1", 0, StepOutcome.SUCCESS),
        _make_step_result("seq1", 1, StepOutcome.REGRESSION, broken=["inv_a"]),
    ]
    metrics = compute_metrics(steps)

    csv_path = tmp_path / "metrics.csv"
    png_path = tmp_path / "metrics.png"

    export_csv(metrics, csv_path)
    assert csv_path.is_file()
    content = csv_path.read_text(encoding="utf-8")
    assert "PROVISIONAL, reference oracle only" in content

    plotted = plot_metrics(metrics, png_path)
    assert plotted is True
    assert png_path.is_file()
    assert png_path.stat().st_size > 0
