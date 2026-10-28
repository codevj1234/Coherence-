"""Table-driven tests for inventory device-name resolution."""

import pytest
from agent.prompts.resolve import ResolvedOutput, resolve_devices


RESOLVE_TEST_CASES = [
    # 1. Exact match
    (
        {"router-1": "hostname router-1"},
        ["router-1", "router-2"],
        {"router-1": "hostname router-1"},
        0,  # warnings
        0,  # errors
    ),
    # 2. Case difference
    (
        {"ROUTER-1": "hostname router-1"},
        ["router-1", "router-2"],
        {"router-1": "hostname router-1"},
        1,  # warning for case resolution
        0,  # errors
    ),
    # 3. Extra whitespace
    (
        {"  router-1  ": "hostname router-1"},
        ["router-1", "router-2"],
        {"router-1": "hostname router-1"},
        1,  # warning for trimming
        0,  # errors
    ),
    # 4. Unknown device name not in inventory
    (
        {"unknown-firewall": "hostname fw"},
        ["router-1", "router-2"],
        {},
        0,  # warnings
        1,  # error: not in inventory
    ),
    # 5. Ambiguous inventory (two devices differing only by case)
    (
        {"router-1": "hostname router-1"},
        ["router-1", "ROUTER-1"],
        {},
        0,  # warnings
        1,  # error: ambiguous match
    ),
    # 6. Empty parsed output
    (
        {},
        ["router-1", "router-2"],
        {},
        0,
        0,
    ),
    # 7. Multiple devices: mix of exact match, case difference, and whitespace
    (
        {"r1": "cfg1", "R2": "cfg2", " r3 ": "cfg3"},
        ["r1", "r2", "r3"],
        {"r1": "cfg1", "r2": "cfg2", "r3": "cfg3"},
        2,  # warnings for R2 and " r3 "
        0,
    ),
    # 8. Partial failure: valid device + unknown device
    (
        {"r1": "cfg1", "bad-device": "cfg-bad"},
        ["r1", "r2"],
        {"r1": "cfg1"},
        0,  # warnings
        1,  # error for bad-device
    ),
]


@pytest.mark.parametrize("parsed_devices,inventory,expected_devices,expected_warnings_count,expected_errors_count", RESOLVE_TEST_CASES)
def test_resolve_devices_table(
    parsed_devices: dict[str, str],
    inventory: list[str],
    expected_devices: dict[str, str],
    expected_warnings_count: int,
    expected_errors_count: int,
) -> None:
    res = resolve_devices(parsed_devices, inventory)
    assert res.devices == expected_devices
    assert len(res.warnings) == expected_warnings_count
    assert len(res.errors) == expected_errors_count
    if expected_errors_count == 0:
        assert res.is_ok is True
    else:
        assert res.is_ok is False
