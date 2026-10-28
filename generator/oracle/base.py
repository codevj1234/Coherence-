"""Abstract base class and models for verification oracles."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from agent.contract.models import Invariant


@dataclass(frozen=True)
class FailureDetail:
    """Detailed diagnosis of an invariant failure."""

    invariant_id: str
    passed: bool
    blocking_device: str | None = None
    blocking_acl: str | None = None
    blocking_line: int | None = None
    rule_text: str | None = None
    reason: str = ""


class Oracle(ABC):
    """Abstract policy verification oracle checking invariants against device configurations."""

    @abstractmethod
    def check(self, configs: dict[str, str], invariants: list[Invariant]) -> dict[str, bool]:
        """Evaluate a collection of invariants against device configurations.

        Args:
            configs: Mapping of device name to full configuration string.
            invariants: List of policy invariants to verify.

        Returns:
            Mapping of invariant ID to boolean pass/fail status.
        """

    @abstractmethod
    def explain_failure(self, configs: dict[str, str], invariant: Invariant) -> FailureDetail:
        """Provide detailed root-cause diagnosis for why an invariant failed."""
