"""Tests for StepLogger crash-resilience and resume helper."""

from pathlib import Path
from agent.contract.models import Condition, StepOutcome, StepResult
from agent.contract.io import read_jsonl
from agent.logging_.step_log import StepLogger, get_completed_steps


def _make_sample_result(run_id: str, sequence_id: str, step: int) -> StepResult:
    return StepResult(
        run_id=run_id,
        model="mock",
        condition=Condition.C0,
        sequence_id=sequence_id,
        step=step,
        parse_ok=True,
        new_intent_ok=True,
        broken_invariants=[],
        outcome=StepOutcome.SUCCESS,
        retries=0,
        tokens_in=100,
        tokens_out=25,
        latency_ms=10,
        committed=True,
    )


def test_step_logger_appends_and_flushes(tmp_path: Path) -> None:
    log_path = tmp_path / "sub" / "run_log.jsonl"
    logger = StepLogger(log_path)

    res1 = _make_sample_result("run-1", "seq-A", 0)
    res2 = _make_sample_result("run-1", "seq-A", 1)

    logger.log(res1)
    logger.log(res2)

    loaded = list(read_jsonl(log_path, StepResult))
    assert len(loaded) == 2
    assert loaded[0].step == 0
    assert loaded[1].step == 1


def test_get_completed_steps_non_existent(tmp_path: Path) -> None:
    steps = get_completed_steps(tmp_path / "missing.jsonl")
    assert steps == set()


def test_get_completed_steps_from_file(tmp_path: Path) -> None:
    log_path = tmp_path / "run.jsonl"
    logger = StepLogger(log_path)

    logger.log(_make_sample_result("run-1", "seq-A", 0))
    logger.log(_make_sample_result("run-1", "seq-A", 1))
    logger.log(_make_sample_result("run-2", "seq-B", 0))

    completed = get_completed_steps(log_path)
    assert completed == {
        ("run-1", "seq-A", 0),
        ("run-1", "seq-A", 1),
        ("run-2", "seq-B", 0),
    }


def test_get_completed_steps_handles_corrupt_trailing_line(tmp_path: Path) -> None:
    log_path = tmp_path / "corrupt.jsonl"
    logger = StepLogger(log_path)
    logger.log(_make_sample_result("run-1", "seq-A", 0))

    # Append a corrupted / half-written line
    with log_path.open("a", encoding="utf-8") as f:
        f.write('{"run_id": "run-1", "sequence_id": "seq-A", "step": 1, "incom\n')

    completed = get_completed_steps(log_path)
    assert completed == {("run-1", "seq-A", 0)}
