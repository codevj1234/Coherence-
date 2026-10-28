"""Oracle package exposing Oracle interfaces, ReferenceOracle, MockOracle, and BatfishOracle."""

from generator.oracle.base import FailureDetail, Oracle
from generator.oracle.batfish import BatfishNotAvailableError, BatfishOracle
from generator.oracle.mock import MockOracle
from generator.oracle.reference import ReferenceOracle

__all__ = [
    "Oracle",
    "FailureDetail",
    "MockOracle",
    "ReferenceOracle",
    "BatfishOracle",
    "BatfishNotAvailableError",
]
