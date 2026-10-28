"""JSONL serialization, strict deserialization with line-number reporting, and schema export.

Provides safe file I/O operations for data contract models used across the pipeline.
All reading operations validate against Pydantic schemas immediately, preventing
corrupted or malformed inputs from propagating downstream.
"""

from collections.abc import Iterator
import json
from pathlib import Path
from typing import Any, TypeVar
from pydantic import BaseModel, ValidationError

from agent.contract.models import Intent, Invariant, StepResult

T = TypeVar("T", bound=BaseModel)


class JSONLValidationError(ValueError):
    """Raised when a JSONL line cannot be parsed as JSON or fails schema validation."""

    def __init__(self, message: str, line_no: int, path: str | Path) -> None:
        super().__init__(f"[{path}:{line_no}] {message}")
        self.line_no = line_no
        self.path = str(path)


def read_jsonl(path: str | Path, model_cls: type[T]) -> Iterator[T]:
    """Yield validated model instances from a JSONL file.

    Reports errors with 1-indexed line numbers and file path for rapid debugging.
    Empty or whitespace-only lines are skipped.

    Args:
        path: Path to the JSONL file to read.
        model_cls: Pydantic model class to validate each record against.

    Yields:
        Validated instances of `model_cls`.

    Raises:
        FileNotFoundError: If the specified file does not exist.
        JSONLValidationError: If any line contains invalid JSON or violates the model schema.
    """
    file_path = Path(path)
    if not file_path.is_file():
        raise FileNotFoundError(f"JSONL file not found: {file_path}")

    with file_path.open("r", encoding="utf-8") as f:
        for line_no, raw_line in enumerate(f, start=1):
            line = raw_line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError as err:
                raise JSONLValidationError(
                    f"Malformed JSON: {err.msg}",
                    line_no=line_no,
                    path=file_path,
                ) from err

            if not isinstance(data, dict):
                raise JSONLValidationError(
                    f"Expected JSON object (dict), got {type(data).__name__}",
                    line_no=line_no,
                    path=file_path,
                )

            try:
                yield model_cls.model_validate(data)
            except ValidationError as err:
                raise JSONLValidationError(
                    f"Validation error: {err}",
                    line_no=line_no,
                    path=file_path,
                ) from err


def append_jsonl(path: str | Path, obj: BaseModel | dict[str, Any]) -> None:
    """Safely append a single model instance or dictionary as a JSON line.

    Creates parent directories if necessary and flushes immediately to prevent
    corrupted records upon unexpected termination.

    Args:
        path: Path to the target JSONL file.
        obj: Pydantic model instance or serializable dictionary.
    """
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    if isinstance(obj, BaseModel):
        line = obj.model_dump_json()
    else:
        line = json.dumps(obj)

    with file_path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()


def export_schemas(output_dir: str | Path | None = None) -> list[Path]:
    """Export standard JSON Schema definitions for shared contract models.

    Allows teammates working in other languages or subsystems (Batfish test bed,
    sequence generator) to validate their files without depending on Python runtime.

    Args:
        output_dir: Target directory for schemas. Defaults to `agent/contract/schemas`.

    Returns:
        List of paths to generated schema files.
    """
    primary_dir = (
        Path(output_dir) if output_dir is not None else Path(__file__).resolve().parent / "schemas"
    )
    target_dirs = [primary_dir]

    # If default was used, also mirror to repo-root contract/schemas for teammate convenience
    if output_dir is None:
        root_contract_schemas = Path(__file__).resolve().parent.parent.parent / "contract" / "schemas"
        target_dirs.append(root_contract_schemas)

    models_to_export: dict[str, type[BaseModel]] = {
        "intent.schema.json": Intent,
        "invariant.schema.json": Invariant,
        "step_result.schema.json": StepResult,
    }

    generated_paths: list[Path] = []
    for target_dir in target_dirs:
        target_dir.mkdir(parents=True, exist_ok=True)
        for filename, model_cls in models_to_export.items():
            schema_path = target_dir / filename
            schema_json = json.dumps(model_cls.model_json_schema(), indent=2)
            schema_path.write_text(schema_json + "\n", encoding="utf-8")
            generated_paths.append(schema_path)

    return generated_paths


if __name__ == "__main__":
    # Allow running directly: python -m agent.contract.io
    exported = export_schemas()
    for p in exported:
        print(f"Exported schema: {p}")
