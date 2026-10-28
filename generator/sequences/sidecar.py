"""Sidecar manifest model and canonical serialization for benchmark sequences."""

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any


@dataclass
class TrapManifestEntry:
    """Metadata for a single trap step."""

    step: int
    kind: str
    broken_invariant_id: str
    naive_description: str


@dataclass
class SequenceManifest:
    """Full sidecar manifest describing the generation provenance of a sequence."""

    sequence_id: str
    topology_id: str
    split: str
    seed: int
    generator_version: str
    length: int
    trap_count: int
    trap_ratio: float
    traps: dict[str, dict[str, Any]]
    invariants_added: dict[str, dict[str, Any]]
    jsonl_sha256: str

    def to_dict(self) -> dict[str, Any]:
        """Convert manifest to sorted dictionary."""
        data = asdict(self)
        return data

    def to_json(self) -> str:
        """Serialize canonically with sorted keys and standard delimiters."""
        return json.dumps(self.to_dict(), sort_keys=True, indent=2) + "\n"

    def write_file(self, path: str | Path) -> None:
        """Write canonical JSON manifest to destination path."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(self.to_json(), encoding="utf-8")


def compute_file_sha256(path: str | Path) -> str:
    """Calculate the SHA-256 hash of a file."""
    p = Path(path)
    hasher = hashlib.sha256()
    with p.open("rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()
