"""Tests for data contract models and IO utilities."""

from pathlib import Path
import pytest
from pydantic import ValidationError

from agent.contract.models import (
    Condition,
    Intent,
    IntentType,
    Invariant,
    InvariantKind,
    StepOutcome,
    StepResult,
)
from agent.contract.io import (
    JSONLValidationError,
    append_jsonl,
    export_schemas,
    read_jsonl,
)


def test_intent_valid() -> None:
    intent = Intent(
        id="intent-1",
        sequence_id="seq-alpha",
        step=0,
        type=IntentType.ACL_PERMIT,
        params={"proto": "tcp", "dst_port": 80},
        text="Permit TCP port 80 from host A to host B",
        adds_invariant="inv-1",
        gold_patch={"router-1": "ip access-list extended ..."},
    )
    assert intent.id == "intent-1"
    assert intent.type == IntentType.ACL_PERMIT
    assert intent.gold_patch == {"router-1": "ip access-list extended ..."}


def test_intent_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Intent(
            id="intent-1",
            sequence_id="seq-alpha",
            step=0,
            type=IntentType.ACL_PERMIT,
            params={},
            text="Permit traffic",
            adds_invariant="inv-1",
            unauthorized_field="illegal",  # type: ignore[call-arg]
        )


def test_invariant_valid() -> None:
    inv = Invariant(
        id="inv-1",
        kind=InvariantKind.REACH,
        source="10.0.0.1/32",
        destination="10.0.1.1/32",
        headers={"protocol": "tcp", "ports": [80, 443]},
        via=None,
        created_at_step=0,
    )
    assert inv.id == "inv-1"
    assert inv.kind == InvariantKind.REACH
    assert inv.via is None


def test_invariant_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Invariant(
            id="inv-1",
            kind=InvariantKind.ISOLATE,
            source="10.0.0.1",
            destination="10.0.2.1",
            headers={},
            created_at_step=1,
            extra_payload="bad",  # type: ignore[call-arg]
        )


def test_step_result_valid() -> None:
    res = StepResult(
        run_id="run-100",
        model="mock-echo",
        condition=Condition.C0,
        sequence_id="seq-alpha",
        step=1,
        parse_ok=True,
        new_intent_ok=True,
        broken_invariants=[],
        outcome=StepOutcome.SUCCESS,
        retries=0,
        tokens_in=120,
        tokens_out=45,
        latency_ms=15,
        committed=True,
    )
    assert res.outcome == StepOutcome.SUCCESS
    assert res.condition == Condition.C0
    assert res.tokens_estimated is False


def test_step_result_tokens_estimated_explicit() -> None:
    res = StepResult(
        run_id="run-101",
        model="mock-echo",
        condition=Condition.C0,
        sequence_id="seq-alpha",
        step=2,
        parse_ok=True,
        new_intent_ok=True,
        broken_invariants=[],
        outcome=StepOutcome.SUCCESS,
        retries=0,
        tokens_in=120,
        tokens_out=45,
        tokens_estimated=True,
        latency_ms=15,
        committed=True,
    )
    assert res.tokens_estimated is True


def test_step_result_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        StepResult(
            run_id="run-100",
            model="mock",
            condition=Condition.C1,
            sequence_id="seq-1",
            step=0,
            parse_ok=True,
            new_intent_ok=False,
            broken_invariants=["inv-0"],
            outcome=StepOutcome.REGRESSION,
            retries=1,
            tokens_in=100,
            tokens_out=50,
            latency_ms=200,
            committed=False,
            unexpected_data=42,  # type: ignore[call-arg]
        )


def test_append_and_read_jsonl(tmp_path: Path) -> None:
    file_path = tmp_path / "intents.jsonl"
    intent1 = Intent(
        id="i-1",
        sequence_id="seq-1",
        step=0,
        type=IntentType.ACL_PERMIT,
        params={},
        text="Allow HTTP",
        adds_invariant="inv-1",
    )
    intent2 = Intent(
        id="i-2",
        sequence_id="seq-1",
        step=1,
        type=IntentType.ACL_DENY,
        params={},
        text="Block SSH",
        adds_invariant="inv-2",
    )

    append_jsonl(file_path, intent1)
    append_jsonl(file_path, intent2)

    loaded = list(read_jsonl(file_path, Intent))
    assert len(loaded) == 2
    assert loaded[0].id == "i-1"
    assert loaded[1].id == "i-2"


def test_read_jsonl_skips_empty_lines(tmp_path: Path) -> None:
    file_path = tmp_path / "with_blanks.jsonl"
    file_path.write_text(
        '{"id":"i-1","sequence_id":"s","step":0,"type":"acl_permit","params":{},"text":"t","adds_invariant":"inv-1"}\n\n   \n'
        '{"id":"i-2","sequence_id":"s","step":1,"type":"acl_deny","params":{},"text":"t","adds_invariant":"inv-2"}\n',
        encoding="utf-8",
    )
    loaded = list(read_jsonl(file_path, Intent))
    assert len(loaded) == 2


def test_read_jsonl_reports_line_number_on_malformed_json(tmp_path: Path) -> None:
    file_path = tmp_path / "corrupt.jsonl"
    file_path.write_text(
        '{"id":"i-1","sequence_id":"s","step":0,"type":"acl_permit","params":{},"text":"t","adds_invariant":"inv-1"}\n'
        'NOT_JSON_LINE\n',
        encoding="utf-8",
    )
    with pytest.raises(JSONLValidationError) as excinfo:
        list(read_jsonl(file_path, Intent))
    assert excinfo.value.line_no == 2
    assert "Malformed JSON" in str(excinfo.value)


def test_read_jsonl_reports_line_number_on_schema_violation(tmp_path: Path) -> None:
    file_path = tmp_path / "schema_violation.jsonl"
    file_path.write_text(
        '{"id":"i-1","sequence_id":"s","step":0,"type":"acl_permit","params":{},"text":"t","adds_invariant":"inv-1"}\n'
        '{"id":"i-2","sequence_id":"s","step":1,"type":"acl_deny","params":{},"text":"t","adds_invariant":"inv-2","extra_forbidden_key":123}\n',
        encoding="utf-8",
    )
    with pytest.raises(JSONLValidationError) as excinfo:
        list(read_jsonl(file_path, Intent))
    assert excinfo.value.line_no == 2
    assert "extra_forbidden_key" in str(excinfo.value)


def test_read_jsonl_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        list(read_jsonl(tmp_path / "non_existent.jsonl", Intent))


def test_export_schemas(tmp_path: Path) -> None:
    target = tmp_path / "schemas"
    generated = export_schemas(target)
    assert len(generated) == 3
    schema_names = {p.name for p in generated}
    assert schema_names == {
        "intent.schema.json",
        "invariant.schema.json",
        "step_result.schema.json",
    }
    for p in generated:
        content = p.read_text(encoding="utf-8")
        assert "properties" in content
