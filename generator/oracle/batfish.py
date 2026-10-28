"""Batfish verification oracle adapter.

Wraps Pybatfish when installed and connects to an active Batfish service container.
When Pybatfish is missing or Batfish is unreachable, marks availability as False
and allows test suites and pipelines to cleanly skip Batfish-dependent tests.
"""

from agent.contract.models import Invariant
from generator.oracle.base import FailureDetail, Oracle


class BatfishNotAvailableError(RuntimeError):
    """Raised when Batfish verification is invoked but Batfish is unavailable."""


class BatfishOracle(Oracle):
    """Adapter wrapping Pybatfish verification queries."""

    def __init__(self, host: str = "localhost", snapshot_dir: str | None = None) -> None:
        self.host = host
        self.snapshot_dir = snapshot_dir
        self._available = False
        self._client = None
        self._check_availability()

    def _check_availability(self) -> None:
        """Probe for pybatfish module and active service reachability."""
        try:
            import pybatfish  # type: ignore[import-not-found]
            from pybatfish.client.session import Session  # type: ignore[import-not-found]

            session = Session(host=self.host)
            # Try a lightweight query to test daemon connectivity
            session.check_version()
            self._client = session
            self._available = True
        except Exception:
            self._available = False
            self._client = None

    @property
    def is_available(self) -> bool:
        """True if Pybatfish is installed and the Batfish service responds."""
        return self._available

    def check(self, configs: dict[str, str], invariants: list[Invariant]) -> dict[str, bool]:
        """Execute Batfish reachability analysis queries."""
        if not self.is_available:
            raise BatfishNotAvailableError(
                "Batfish service is unavailable. Ensure pybatfish is installed "
                "and Batfish Docker container is running."
            )
        # Placeholder for teammate Batfish integration
        raise NotImplementedError("Teammate Batfish test bed integration pending.")

    def explain_failure(self, configs: dict[str, str], invariant: Invariant) -> FailureDetail:
        """Provide Batfish-derived failure diagnosis."""
        if not self.is_available:
            raise BatfishNotAvailableError("Batfish service is unavailable.")
        raise NotImplementedError("Teammate Batfish test bed integration pending.")
