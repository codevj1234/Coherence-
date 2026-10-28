"""Mock verification oracle that unconditionally passes all invariants."""

from agent.contract.models import Invariant
from generator.oracle.base import FailureDetail, Oracle


class MockOracle(Oracle):
    """Deterministic mock oracle for offline pipeline testing and dry runs."""

    def check(self, configs: dict[str, str], invariants: list[Invariant]) -> dict[str, bool]:
        """Unconditionally pass every invariant."""
        return {inv.id: True for inv in invariants}

    def explain_failure(self, configs: dict[str, str], invariant: Invariant) -> FailureDetail:
        """Return a passed detail since MockOracle never fails."""
        return FailureDetail(invariant_id=invariant.id, passed=True, reason="MockOracle unconditionally passes.")
