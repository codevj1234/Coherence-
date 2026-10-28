"""Inventory-aware device name resolution.

Decoupled from raw syntax parsing to preserve separation of concerns:
parse_model_output operates purely on syntactic markdown structures, while
resolve_devices validates and normalizes device identifiers against the ground-truth
topology inventory.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass
class ResolvedOutput:
    """Outcome of resolving raw parsed device names against topology inventory."""

    devices: dict[str, str]
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def is_ok(self) -> bool:
        """True if all devices resolved without error."""
        return len(self.errors) == 0


def resolve_devices(
    parsed_devices: dict[str, str],
    inventory: Iterable[str],
) -> ResolvedOutput:
    """Resolve raw parsed device names against known topology inventory.

    Rules:
    - Match case-insensitively after trimming whitespace.
    - Map to the exact inventory name.
    - Add a warning for each name that required whitespace/case normalization.
    - If a name matches nothing in inventory, record an error (never create new devices).
    - If inventory contains multiple names differing only by case, treat any match
      as ambiguous and record an error.
    - Never raises exceptions on bad input.

    Args:
        parsed_devices: Raw device name to configuration text mapping.
        inventory: Iterable of valid canonical device names in the topology.

    Returns:
        ResolvedOutput containing mapped devices, warnings, and errors.
    """
    resolved_devices: dict[str, str] = {}
    warnings: list[str] = []
    errors: list[str] = []

    if not parsed_devices:
        return ResolvedOutput(devices={}, warnings=[], errors=[])

    # Index inventory names by lowercased trimmed key to detect ambiguities
    inv_map: dict[str, list[str]] = {}
    for inv_name in inventory:
        key = str(inv_name).strip().lower()
        inv_map.setdefault(key, []).append(str(inv_name))

    for raw_name, config in parsed_devices.items():
        stripped_raw = str(raw_name).strip()
        key = stripped_raw.lower()

        if key not in inv_map or len(inv_map[key]) == 0:
            errors.append(f"Device '{raw_name}' does not match any device in topology inventory.")
            continue

        matching_inv_names = inv_map[key]
        if len(matching_inv_names) > 1:
            errors.append(
                f"Ambiguous device match for '{raw_name}': multiple inventory devices differ only by case {matching_inv_names}."
            )
            continue

        canonical_name = matching_inv_names[0]
        if raw_name != canonical_name:
            warnings.append(
                f"Resolved device name '{raw_name}' to canonical inventory name '{canonical_name}'."
            )

        if canonical_name in resolved_devices:
            warnings.append(
                f"Multiple parsed blocks resolved to the same canonical device '{canonical_name}'; last instance used."
            )

        resolved_devices[canonical_name] = config

    return ResolvedOutput(devices=resolved_devices, warnings=warnings, errors=errors)
