"""Traps package for generating provably regressive trap change steps."""

from generator.traps.catalog import generate_trap_step
from generator.traps.isolation_leak import build_isolation_leak_trap
from generator.traps.prefix_overlap import build_prefix_overlap_trap
from generator.traps.shadowing import TrapResult, build_shadowing_trap

__all__ = [
    "TrapResult",
    "generate_trap_step",
    "build_shadowing_trap",
    "build_prefix_overlap_trap",
    "build_isolation_leak_trap",
]
