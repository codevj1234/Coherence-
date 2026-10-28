"""Pydantic data models for network policy regression benchmarks.

These models define the strict data contract shared between the sequence generator,
the LLM agent harness, and the Batfish verification harness. Strict validation with
extra='forbid' prevents silent schema drift or accidental field mismatches across
independently developed project components.
"""

from enum import StrEnum
from typing import Any
from pydantic import BaseModel, ConfigDict, Field


class IntentType(StrEnum):
    """Supported network intent operational categories.

    Each category corresponds to a specific type of configuration transformation
    tested against the target topology.
    """

    ACL_PERMIT = "acl_permit"
    ACL_DENY = "acl_deny"
    VRF_ISOLATE = "vrf_isolate"
    PREFIX_FILTER = "prefix_filter"
    WAYPOINT = "waypoint"


class InvariantKind(StrEnum):
    """Semantic category of invariant policy rules.

    Used by verification oracles to determine reachability, isolation, or traffic pathing.
    """

    REACH = "reach"
    ISOLATE = "isolate"
    WAYPOINT = "waypoint"


class Condition(StrEnum):
    """Experimental conditions varying the context and feedback provided to the LLM.

    C0: no help (open-loop baseline)
    C1: checker feedback + up to 3 retries
    C2: prompt lists only the affected rules
    C3: full guard (bad edits are rejected and never committed)
    """

    C0 = "C0"
    C1 = "C1"
    C2 = "C2"
    C3 = "C3"


class StepOutcome(StrEnum):
    """Classification of the verification result for a single configuration step.

    SUCCESS: New intent satisfied and no existing invariants broken.
    FORWARD_FAIL: New intent could not be satisfied, but no past invariants broken.
    REGRESSION: New intent satisfied, but one or more past invariants broken.
    BOTH: New intent failed AND past invariants broken.
    PARSE_FAIL: Output from LLM could not be parsed into valid device configuration blocks.
    REJECTED: LLM explicitly refused to perform the edit or generated an invalid response.
    """

    SUCCESS = "SUCCESS"
    FORWARD_FAIL = "FORWARD_FAIL"
    REGRESSION = "REGRESSION"
    BOTH = "BOTH"
    PARSE_FAIL = "PARSE_FAIL"
    REJECTED = "REJECTED"


class Intent(BaseModel):
    """Single incremental policy change request presented to the LLM.

    Contains the natural language prompt text shown to the model, machine-readable
    parameters used by generators/verifiers, and optional ground-truth configuration
    patches used by evaluation oracles.
    """

    model_config = ConfigDict(extra="allow")

    id: str = Field(description="Unique identifier for the intent.")
    sequence_id: str = Field(description="Identifier of the sequence this intent belongs to.")
    step: int = Field(ge=0, description="Zero-based sequence step index.")
    type: IntentType = Field(description="Operational category of the policy change.")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Machine-readable parameters (e.g., IPs, ports, protocols, device targets).",
    )
    text: str = Field(description="Natural-language instruction displayed to the LLM.")
    adds_invariant: str = Field(
        description="Identifier of the invariant introduced by this intent that must hold henceforth."
    )
    gold_patch: dict[str, str] | None = Field(
        default=None,
        description="Optional ground truth device configurations; must NEVER be exposed to prompts.",
    )


class Invariant(BaseModel):
    """Policy invariant that must be maintained throughout configuration evolution.

    Invariants represent ground-truth contractual guarantees (reachability, isolation,
    or waypoint routing) established at earlier steps that future changes must not regress.
    """

    model_config = ConfigDict(extra="allow")

    id: str = Field(description="Unique identifier for the invariant.")
    kind: InvariantKind = Field(description="Invariant policy kind (reach, isolate, waypoint).")
    source: str = Field(description="Source endpoint or CIDR prefix.")
    destination: str = Field(description="Destination endpoint or CIDR prefix.")
    headers: dict[str, Any] = Field(
        description="Network header constraints (e.g. protocol, source/destination ports)."
    )
    via: str | None = Field(
        default=None,
        description="Optional waypoint node name or transit hop requirement.",
    )
    created_at_step: int = Field(ge=0, description="Step index when this invariant was introduced.")


class StepResult(BaseModel):
    """Record of an evaluated LLM interaction step.

    Appended to JSONL run logs to record parsing success, verification outcomes,
    token consumption, wall-clock latency, and cuc harness telemetry for statistical analysis.
    """

    model_config = ConfigDict(extra="allow")

    run_id: str = Field(description="Unique evaluation run identifier.")
    model: str = Field(description="Model identifier or configuration name under test.")
    condition: Condition = Field(description="Experimental context condition (C0-C3).")
    sequence_id: str = Field(description="Identifier of the benchmark sequence.")
    step: int = Field(ge=0, description="Step index within the sequence.")
    parse_ok: bool = Field(description="True if model output successfully parsed into device configs.")
    new_intent_ok: bool = Field(
        description="True if the new intent was satisfied according to the verification oracle."
    )
    broken_invariants: list[str] = Field(
        default_factory=list,
        description="List of invariant IDs that were violated by the committed configuration.",
    )
    outcome: StepOutcome = Field(description="Summary outcome classification.")
    retries: int = Field(ge=0, default=0, description="Number of retries attempted in this step.")
    tokens_in: int = Field(ge=0, description="Input tokens consumed.")
    tokens_out: int = Field(ge=0, description="Output tokens generated.")
    tokens_estimated: bool = Field(
        default=False,
        description="True if token counts were estimated using len(text)/4 heuristic instead of provider usage metadata.",
    )
    latency_ms: int = Field(ge=0, description="Elapsed wall-clock latency in milliseconds.")
    committed: bool = Field(
        description="Whether the patch was committed to the active topology configuration."
    )
    violated_now: int = Field(default=0, description="Number of invariant violations at the current step.")
    model_errors: list[str] = Field(default_factory=list, description="Errors produced by the model output.")
    first_attempt_regression: bool = Field(default=False, description="True if regression occurred on first attempt.")
    intent_type: str | None = Field(default=None, description="Type of intent being processed.")
    model_name: str | None = Field(default=None, description="Model name identifier.")
    resumed: bool = Field(default=False, description="True if step was resumed from checkpoint.")


