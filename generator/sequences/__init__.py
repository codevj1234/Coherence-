"""Sequences package for building reproducible, verified benchmark sequences."""

from generator.sequences.builder import (
    GeneratedSequence,
    build_sequence,
    save_sequence_files,
)
from generator.sequences.contradictions import is_contradictory
from generator.sequences.sidecar import SequenceManifest, TrapManifestEntry

__all__ = [
    "build_sequence",
    "save_sequence_files",
    "GeneratedSequence",
    "SequenceManifest",
    "TrapManifestEntry",
    "is_contradictory",
]
