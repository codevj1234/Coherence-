"""Prompt construction for network policy regression benchmarks.

Assembles system and user messages tailored to the active experimental condition
(C0, C1, C2, C3). Strictly prevents prompt contamination: ground truth patches
are excluded structurally by rejecting Intent objects with gold_patch fields,
while legitimately allowing networking terminology such as 'gold-vlan'.
"""

from dataclasses import dataclass
from typing import Any
from agent.contract.models import Condition
from agent.llm.client import Prompt
from agent.prompts.templates import SYSTEM_PROMPT


@dataclass(frozen=True)
class PromptIntent:
    """Safe representation of an intent for prompt construction without gold_patch fields."""

    text: str


def build_prompt(
    device_configs: dict[str, str],
    intent_text: str | PromptIntent,
    condition: Condition | str,
    ledger: str | None = None,
    feedback: str | None = None,
    previous_attempt: dict[str, str] | None = None,
) -> Prompt:
    """Build system and user prompts according to the experimental condition.

    Args:
        device_configs: Mapping from device name to current configuration string.
        intent_text: Natural-language policy intent string or PromptIntent (full Intent forbidden).
        condition: Active experimental condition (C0, C1, C2, C3).
        ledger: Formatted invariant ledger string (used under C3).
        feedback: Verification oracle error report from a previous attempt (used under C1+).
        previous_attempt: Per-device configurations from the previous attempt for this same step.

    Returns:
        Structured Prompt instance with system and user messages.

    Raises:
        TypeError: If an object containing a 'gold_patch' attribute is passed.
        ValueError: If feedback or previous_attempt is provided under condition C0.
    """
    # Structural protection: prevent Intent models carrying gold_patch from entering prompts
    if hasattr(intent_text, "gold_patch"):
        raise TypeError(
            "Structural safety violation: build_prompt rejects full Intent objects "
            "carrying gold_patch. Pass intent.text (str) or PromptIntent instead."
        )

    resolved_intent_text = (
        intent_text.text if isinstance(intent_text, PromptIntent) else str(intent_text)
    )

    cond = Condition(condition) if isinstance(condition, str) else condition

    # Condition C0 does not allow retry feedback or previous attempts
    if cond == Condition.C0:
        if feedback is not None and feedback.strip():
            raise ValueError("Condition C0 does not permit verification feedback.")
        if previous_attempt is not None and len(previous_attempt) > 0:
            raise ValueError("Condition C0 does not permit previous attempt configurations.")

    sections: list[str] = []

    # 1. Base device configurations for this step
    sections.append("### CURRENT DEVICE CONFIGURATIONS")
    for device_name in sorted(device_configs.keys()):
        cfg = device_configs[device_name].strip()
        sections.append(f"```{device_name}\n{cfg}\n```")

    # 2. Invariant ledger (Condition C3)
    if cond == Condition.C3 and ledger is not None and ledger.strip():
        sections.append(
            "### POLICY LEDGER (INVARIANTS THAT MUST STILL HOLD)\n"
            f"{ledger.strip()}"
        )

    # 3. Requested Intent
    sections.append(f"### REQUESTED INTENT\n{resolved_intent_text.strip()}")

    # 4. Previous attempt for this step (Condition C1+)
    if previous_attempt:
        sections.append("### PREVIOUS ATTEMPT FOR THIS STEP (FAILED VERIFICATION)")
        for dev_name in sorted(previous_attempt.keys()):
            cfg = previous_attempt[dev_name].strip()
            sections.append(f"```{dev_name}\n{cfg}\n```")

    # 5. Feedback from previous attempt (Condition C1+)
    if feedback is not None and feedback.strip():
        sections.append(
            "### FEEDBACK ON PREVIOUS ATTEMPT (DO NOT REPEAT THESE FAILURES)\n"
            f"{feedback.strip()}"
        )

    user_text = "\n\n".join(sections)
    system_text = SYSTEM_PROMPT

    return Prompt(system=system_text, user=user_text)
