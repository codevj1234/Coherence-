"""Tests for prompt templates, prompt builder, safety checks, and table-driven output parser."""

import pytest

from agent.contract.models import Condition
from agent.prompts.builder import build_prompt
from agent.prompts.parser import ParsedOutput, parse_model_output
from agent.prompts.templates import PROMPT_VERSION, SYSTEM_PROMPT


def test_prompt_templates() -> None:
    assert PROMPT_VERSION == "v1"
    assert "expert network engineer" in SYSTEM_PROMPT
    assert "OUTPUT FORMAT" in SYSTEM_PROMPT


def test_build_prompt_c0() -> None:
    configs = {"r1": "hostname r1\ninterface eth0", "r2": "hostname r2"}
    prompt = build_prompt(
        device_configs=configs,
        intent_text="Allow SSH from 10.0.0.1 to 10.0.0.2",
        condition=Condition.C0,
    )
    assert "```r1" in prompt.user
    assert "```r2" in prompt.user
    assert "Allow SSH" in prompt.user
    assert "FEEDBACK" not in prompt.user
    assert "POLICY LEDGER" not in prompt.user


def test_build_prompt_c1_with_feedback() -> None:
    configs = {"r1": "hostname r1"}
    prompt = build_prompt(
        device_configs=configs,
        intent_text="Block Telnet",
        condition=Condition.C1,
        feedback="Telnet on port 23 is still reachable.",
    )
    assert "FEEDBACK ON PREVIOUS ATTEMPT" in prompt.user
    assert "Telnet on port 23 is still reachable." in prompt.user
    assert "POLICY LEDGER" not in prompt.user


def test_build_prompt_c3_with_ledger() -> None:
    configs = {"r1": "hostname r1"}
    ledger_text = "Invariant 1: host A must reach host B on TCP 80"
    prompt = build_prompt(
        device_configs=configs,
        intent_text="Allow UDP 53",
        condition=Condition.C3,
        ledger=ledger_text,
    )
    assert "POLICY LEDGER" in prompt.user
    assert ledger_text in prompt.user


def test_build_prompt_step_k_contains_nothing_from_step_k_minus_1() -> None:
    # Step k-1 intent and configurations
    step_0_intent = "Step 0: Configure NTP server 192.168.1.1"
    step_0_feedback = "Step 0 error: NTP sync timed out"

    # Step k intent and current configs
    step_1_intent = "Step 1: Allow DNS queries on port 53"
    current_configs = {"r1": "hostname r1\nntp server 192.168.1.1"}

    prompt_step_1 = build_prompt(
        device_configs=current_configs,
        intent_text=step_1_intent,
        condition=Condition.C0,
    )
    # The prompt for step 1 contains its own intent and current configs
    assert step_1_intent in prompt_step_1.user
    # But contains no historical prompts, intents, or failure messages from step 0
    assert step_0_intent not in prompt_step_1.user
    assert step_0_feedback not in prompt_step_1.user


def test_build_prompt_retry_contains_previous_attempt_and_feedback() -> None:
    configs = {"r1": "hostname r1\ninterface eth0"}
    intent_text = "Permit HTTPS to web server"
    prev_attempt = {"r1": "hostname r1\nip access-list extended ACL\n permit tcp any any eq 80"}
    feedback_text = "Verification failed: HTTPS port 443 is not permitted."

    retry_prompt = build_prompt(
        device_configs=configs,
        intent_text=intent_text,
        condition=Condition.C1,
        feedback=feedback_text,
        previous_attempt=prev_attempt,
    )
    # Both sections must be present and clearly delimited
    assert "### PREVIOUS ATTEMPT FOR THIS STEP (FAILED VERIFICATION)" in retry_prompt.user
    assert "permit tcp any any eq 80" in retry_prompt.user
    assert "### FEEDBACK ON PREVIOUS ATTEMPT (DO NOT REPEAT THESE FAILURES)" in retry_prompt.user
    assert feedback_text in retry_prompt.user


def test_build_prompt_c0_rejects_feedback_and_previous_attempt() -> None:
    configs = {"r1": "hostname r1"}
    # Feedback under C0 must be rejected
    with pytest.raises(ValueError) as exc1:
        build_prompt(
            device_configs=configs,
            intent_text="Allow HTTP",
            condition=Condition.C0,
            feedback="Some feedback",
        )
    assert "Condition C0" in str(exc1.value)

    # Previous attempt under C0 must be rejected
    with pytest.raises(ValueError) as exc2:
        build_prompt(
            device_configs=configs,
            intent_text="Allow HTTP",
            condition=Condition.C0,
            previous_attempt={"r1": "hostname r1"},
        )
    assert "Condition C0" in str(exc2.value)


def test_build_prompt_allows_gold_network_naming() -> None:
    # Gold naming in networking (e.g. gold-vlan, gold QoS tier) is now permitted
    configs = {"r1": "hostname r1\ninterface vlan 100\n name gold-vlan"}
    intent_text = "Configure QoS policy for gold-tier traffic"
    prompt = build_prompt(
        device_configs=configs,
        intent_text=intent_text,
        condition=Condition.C0,
    )
    assert "gold-vlan" in prompt.user
    assert "gold-tier" in prompt.user


def test_build_prompt_rejects_full_intent_with_gold_patch() -> None:
    from agent.contract.models import Intent, IntentType

    secret_patch_content = "super_secret_gold_patch_never_leak_this_58291"
    intent = Intent(
        id="intent-test",
        sequence_id="seq-test",
        step=0,
        type=IntentType.ACL_PERMIT,
        params={},
        text="Permit SSH on gold-vlan",
        adds_invariant="inv-1",
        gold_patch={"r1": secret_patch_content},
    )
    # Passing the full Intent object carrying gold_patch must be rejected structurally
    with pytest.raises(TypeError) as excinfo:
        build_prompt(
            device_configs={"r1": "hostname r1"},
            intent_text=intent,  # type: ignore[arg-type]
            condition=Condition.C0,
        )
    assert "gold_patch" in str(excinfo.value)

    # Passing intent.text or PromptIntent works and never contains the gold patch
    prompt = build_prompt(
        device_configs={"r1": "hostname r1"},
        intent_text=intent.text,
        condition=Condition.C0,
    )
    assert "Permit SSH on gold-vlan" in prompt.user
    assert secret_patch_content not in prompt.user
    assert secret_patch_content not in prompt.system


# Table of at least 15 messy model outputs
MESSY_PARSER_CASES = [
    # 1. Clean single device
    (
        "```r1\nhostname r1\ninterface eth0\n```",
        {"r1": "hostname r1\ninterface eth0"},
        0,  # errors
        0,  # warnings
    ),
    # 2. Clean multi device
    (
        "```r1\nhostname r1\n```\n\n```r2\nhostname r2\n```",
        {"r1": "hostname r1", "r2": "hostname r2"},
        0,
        0,
    ),
    # 3. Windows CRLF endings
    (
        "```r1\r\nhostname r1\r\ninterface GigabitEthernet0/0\r\n```\r\n",
        {"r1": "hostname r1\ninterface GigabitEthernet0/0"},
        0,
        0,
    ),
    # 4. Missing fences (pure conversational text)
    (
        "I have reviewed the network topology and made the requested changes on router 1.",
        {},
        1,  # "No fenced configuration blocks found..."
        1,  # warning for prose
    ),
    # 5. Unclosed fence at EOF
    (
        "```r1\nhostname r1\ninterface eth0",
        {"r1": "hostname r1\ninterface eth0"},
        1,  # unclosed fence error
        0,
    ),
    # 6. Duplicate device blocks (last wins)
    (
        "```r1\nhostname r1_old\n```\n```r1\nhostname r1_new\n```",
        {"r1": "hostname r1_new"},
        1,  # duplicate block error
        0,
    ),
    # 7. Empty block
    (
        "```r1\n```",
        {},
        1,  # empty block error
        0,
    ),
    # 8. Block with only whitespace
    (
        "```r1\n   \n\t  \n   \n```",
        {},
        1,  # empty block error
        0,
    ),
    # 9. Extra conversational prose before block
    (
        "Sure, here is the updated configuration for r1:\n```r1\nhostname r1\n```",
        {"r1": "hostname r1"},
        0,  # errors
        1,  # warning for leading prose
    ),
    # 10. Extra conversational prose after block
    (
        "```r1\nhostname r1\n```\nLet me know if you need any other modifications!",
        {"r1": "hostname r1"},
        0,  # errors
        1,  # warning for trailing prose
    ),
    # 11. Conversational prose between two blocks
    (
        "```r1\nhostname r1\n```\nAnd now for router 2:\n```r2\nhostname r2\n```",
        {"r1": "hostname r1", "r2": "hostname r2"},
        0,  # errors
        1,  # warning for middle prose
    ),
    # 12. Fence without device name
    (
        "```\nhostname r1\n```",
        {},
        1,  # missing device name error
        0,
    ),
    # 13. Fence with extra whitespace around device name
    (
        "```   router-core.1   \nhostname router-core.1\n```",
        {"router-core.1": "hostname router-core.1"},
        0,
        0,
    ),
    # 14. Fence with multi-token tag (e.g. ```r1 cisco-ios)
    (
        "```r1 cisco-ios\nhostname r1\n```",
        {"r1": "hostname r1"},
        0,
        0,
    ),
    # 15. Empty string input
    (
        "",
        {},
        1,  # empty response error
        0,
    ),
    # 16. Only whitespace / newlines
    (
        "   \n\n\t   \n",
        {},
        1,
        0,
    ),
    # 17. Multi-device where second device fence is unclosed
    (
        "```r1\nhostname r1\n```\n```r2\nhostname r2_unclosed",
        {"r1": "hostname r1", "r2": "hostname r2_unclosed"},
        1,  # unclosed r2
        0,
    ),
    # 18. Triple duplicate device block (last wins)
    (
        "```r1\nv1\n```\n```r1\nv2\n```\n```r1\nv3\n```",
        {"r1": "v3"},
        2,  # two duplicate warnings/errors
        0,
    ),
    # 19. Inline backticks inside prose without block
    (
        "Please execute `show running-config` on `r1` and `r2`.",
        {},
        1,  # no fenced blocks found
        1,  # prose warning
    ),
    # 20. Config containing special symbols and comments
    (
        "```sw1\n!\n! System Configuration\n!\nip access-list extended TEST_#1:2\n permit tcp host 1.1.1.1 any eq 80\n```",
        {"sw1": "!\n! System Configuration\n!\nip access-list extended TEST_#1:2\n permit tcp host 1.1.1.1 any eq 80"},
        0,
        0,
    ),
]


@pytest.mark.parametrize("raw_input,expected_devices,expected_min_errors,expected_min_warnings", MESSY_PARSER_CASES)
def test_parse_model_output_table(
    raw_input: str,
    expected_devices: dict[str, str],
    expected_min_errors: int,
    expected_min_warnings: int,
) -> None:
    res = parse_model_output(raw_input)
    assert res.devices == expected_devices
    assert len(res.errors) >= expected_min_errors, f"Expected >={expected_min_errors} errors, got: {res.errors}"
    assert len(res.warnings) >= expected_min_warnings, f"Expected >={expected_min_warnings} warnings, got: {res.warnings}"


# Harder parser test cases asserting EXACT devices, errors, and warnings (Change 5)
HARDER_PARSER_CASES = [
    # 1. Language tag after device name on fence line
    (
        "```r1 ios\nhostname r1\ninterface GigabitEthernet0/0\n```",
        {"r1": "hostname r1\ninterface GigabitEthernet0/0"},
        [],
        [],
    ),
    # 2. Uppercase device name (must be preserved verbatim)
    (
        "```ROUTER-CORE-1\nhostname ROUTER-CORE-1\n```",
        {"ROUTER-CORE-1": "hostname ROUTER-CORE-1"},
        [],
        [],
    ),
    # 3. Fences using four backticks
    (
        "````r1\nhostname r1\n````",
        {"r1": "hostname r1"},
        [],
        [],
    ),
    # 4. Fences using five backticks with language tag
    (
        "`````r2 cisco-ios-xe\nhostname r2\n`````",
        {"r2": "hostname r2"},
        [],
        [],
    ),
    # 5. Tilde fences (3 tildes)
    (
        "~~~sw1\nhostname sw1\n~~~",
        {"sw1": "hostname sw1"},
        [],
        [],
    ),
    # 6. Tilde fences (4 tildes) with language tag
    (
        "~~~~fw1 junos\nset system host-name fw1\n~~~~",
        {"fw1": "set system host-name fw1"},
        [],
        [],
    ),
    # 7. Prose before and after blocks
    (
        "Here is the requested patch:\n```r1\nhostname r1\n```\nConfiguration has been updated successfully.",
        {"r1": "hostname r1"},
        [],
        [
            "Prose outside fenced blocks ignored at line 1: Here is the requested patch:",
            "Prose outside fenced blocks ignored at line 5: Configuration has been updated successfully.",
        ],
    ),
    # 8. Block with trailing spaces on lines and fence
    (
        "```r1   \nhostname r1  \ninterface eth0  \n```",
        {"r1": "hostname r1  \ninterface eth0"},
        [],
        [],
    ),
    # 9. Mixed LF and CRLF
    (
        "```r1\r\nhostname r1\nip route 0.0.0.0 0.0.0.0 10.0.0.1\r\n```\n",
        {"r1": "hostname r1\nip route 0.0.0.0 0.0.0.0 10.0.0.1"},
        [],
        [],
    ),
    # 10. Two blocks for the same device (last wins, error recorded)
    (
        "```r1\nhostname r1_v1\n```\n```r1\nhostname r1_v2\n```",
        {"r1": "hostname r1_v2"},
        ["Duplicate configuration block for device 'r1'; last instance used."],
        [],
    ),
    # 11. Block that is only whitespace
    (
        "```r1\n   \n\t   \n```",
        {},
        ["Empty configuration block for device 'r1'."],
        [],
    ),
    # 12. Unclosed final block at EOF
    (
        "```r1\nhostname r1\ninterface GigabitEthernet0/0",
        {"r1": "hostname r1\ninterface GigabitEthernet0/0"},
        ["Unclosed fenced code block for device 'r1'."],
        [],
    ),
    # 13. Response that contains no blocks at all
    (
        "I reviewed your network request and everything looks already configured.",
        {},
        ["No fenced configuration blocks found in model output."],
        ["Prose outside fenced blocks ignored at line 1: I reviewed your network request and everything looks alre..."],
    ),
    # 14. Four backticks unclosed at EOF
    (
        "````r1\nhostname r1\ninterface eth0",
        {"r1": "hostname r1\ninterface eth0"},
        ["Unclosed fenced code block for device 'r1'."],
        [],
    ),
    # 15. Tilde fence unclosed at EOF
    (
        "~~~sw1\nhostname sw1",
        {"sw1": "hostname sw1"},
        ["Unclosed fenced code block for device 'sw1'."],
        [],
    ),
    # 16. Empty code fence without device name
    (
        "```\nhostname r1\n```",
        {},
        [
            "Line 1: Code block opening fence is missing a device name.",
        ],
        [],
    ),
    # 17. Mismatched fence delimiter (backtick opened, tilde closed) leaving block unclosed
    (
        "```r1\nhostname r1\n~~~",
        {"r1": "hostname r1\n~~~"},
        ["Unclosed fenced code block for device 'r1'."],
        [],
    ),
]


@pytest.mark.parametrize("raw_input,expected_devices,expected_errors,expected_warnings", HARDER_PARSER_CASES)
def test_parse_model_output_harder_exact(
    raw_input: str,
    expected_devices: dict[str, str],
    expected_errors: list[str],
    expected_warnings: list[str],
) -> None:
    res = parse_model_output(raw_input)
    assert res.devices == expected_devices
    assert res.errors == expected_errors
    assert res.warnings == expected_warnings
