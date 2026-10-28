"""Tests for agent/training/export_sft.py."""

from pathlib import Path
import pytest

from agent.training.export_sft import (
    DataLeakageError,
    check_leakage,
    export_sft_dataset,
)


def test_leakage_detection():
    test_seq_ids = {"topo_test_large_test_seq_001"}
    test_topos = {"topo_test_large", "topo_test_small"}
    test_phrases = {"targeting port", "prevent route installation"}

    # Clean text should pass
    check_leakage("Configure router tr_r1 to permit tcp traffic", test_seq_ids, test_topos, test_phrases)

    # Sequence ID leak
    with pytest.raises(DataLeakageError, match="test sequence ID"):
        check_leakage("Found in topo_test_large_test_seq_001 line 5", test_seq_ids, test_topos, test_phrases)

    # Topology leak
    with pytest.raises(DataLeakageError, match="test topology"):
        check_leakage("Using topology topo_test_small for evaluation", test_seq_ids, test_topos, test_phrases)

    # Reserved phrase leak
    with pytest.raises(DataLeakageError, match="reserved phrase"):
        check_leakage("Please configure targeting port 80 now", test_seq_ids, test_topos, test_phrases)


def test_export_sft_dataset_run(tmp_path: Path):
    out_file = tmp_path / "sft_test.jsonl"
    seq_count, ex_count = export_sft_dataset(
        train_dir="generator/data/train",
        data_dir="generator/data",
        output_path=str(out_file),
    )
    assert seq_count == 10
    assert ex_count == 500
    assert out_file.is_file()

    # Check first line schema
    import json
    with out_file.open("r", encoding="utf-8") as f:
        first_line = json.loads(f.readline())
        assert "prompt" in first_line
        assert "response" in first_line
        assert "messages" in first_line
        assert len(first_line["messages"]) == 3
        assert first_line["sequence_id"].startswith("topo_train_")
