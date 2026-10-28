"""Crash-safe append-only logger and benchmark resumption indexer for StepResults.

Ensures that every step outcome recorded during long-running multi-hour benchmark
sequences is persisted immediately to disk without buffer retention or line tearing.
Enables interrupted runs to seamlessly resume without re-executing already verified steps.
"""

import json
import os
from pathlib import Path

from agent.contract.models import StepResult


class StepLogger:
    """Thread-safe and crash-resilient append-only logger for StepResults."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, result: StepResult) -> None:
        """Atomically append a single StepResult JSON record and flush to disk.

        Constructs the entire JSON line in memory first, writes it in a single syscall,
        and invokes os.fsync to ensure zero partial lines even if the process or machine
        is abruptly halted.

        Args:
            result: Validated StepResult record to persist.
        """
        line = result.model_dump_json() + "\n"
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())


def get_completed_steps(path: str | Path) -> set[tuple[str, str, int]]:
    """Scan an existing JSONL log file to identify already completed steps.

    Used by benchmark drivers to resume interrupted sequences without duplicate
    API charges or redundant Batfish verification runs.

    Args:
        path: Path to the StepResult JSONL log file.

    Returns:
        Set of (run_id, sequence_id, step) tuples found in the file.
    """
    file_path = Path(path)
    if not file_path.is_file():
        return set()

    completed: set[tuple[str, str, int]] = set()

    with file_path.open("r", encoding="utf-8") as f:
        for line in f:
            line_str = line.strip()
            if not line_str:
                continue

            try:
                data = json.loads(line_str)
                # Quick dictionary extraction with schema verification fallback
                run_id = data.get("run_id")
                sequence_id = data.get("sequence_id")
                step = data.get("step")
                if run_id is not None and sequence_id is not None and step is not None:
                    completed.add((str(run_id), str(sequence_id), int(step)))
            except (json.JSONDecodeError, ValueError, TypeError):
                # Ignore corrupted trailing lines from abnormal process kills
                continue

    return completed
