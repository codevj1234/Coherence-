"""Audit Check 6: Paraphrase hold-out and vocabulary disjointness."""

import json
from pathlib import Path
from generator.intents.paraphrases import (
    TRAIN_PARAPHRASES,
    TEST_PARAPHRASES,
    TEST_RESERVED_PHRASES,
)
from agent.contract.models import IntentType

def main():
    print("=== AUDIT CHECK 6: PARAPHRASE HOLD-OUT ===")

    all_disjoint = True

    for intent_type in IntentType:
        train_pool = TRAIN_PARAPHRASES.get(intent_type, [])
        test_pool = TEST_PARAPHRASES.get(intent_type, [])

        print(f"\nIntent Type: {intent_type.value.upper()}")
        print(f"  Train pool size: {len(train_pool)}")
        for i, p in enumerate(train_pool, 1):
            print(f"    [Train {i}] {p}")

        print(f"  Test pool size: {len(test_pool)}")
        for i, p in enumerate(test_pool, 1):
            print(f"    [Test {i}] {p}")

        overlap = set(train_pool) & set(test_pool)
        print(f"  Disjoint: {len(overlap) == 0} (Overlap: {overlap})")
        if overlap:
            all_disjoint = False

    print(f"\nAll paraphrase pools strictly disjoint: {all_disjoint}")
    assert all_disjoint

    print("\n--- Reserved Test Phrases Audit ---")
    all_reserved_phrases = []
    for itype, phrases in TEST_RESERVED_PHRASES.items():
        all_reserved_phrases.extend(phrases)
    print(f"Reserved test phrases ({len(all_reserved_phrases)}): {all_reserved_phrases}")

    train_data_dir = Path("generator/data/train")
    train_files = list(train_data_dir.glob("*.jsonl"))
    print(f"Auditing {len(train_files)} sequence files in {train_data_dir}...")

    leak_count = 0
    for tf in train_files:
        content = tf.read_text(encoding="utf-8").lower()
        for phrase in all_reserved_phrases:
            if phrase.lower() in content:
                print(f"LEAK FOUND: Reserved phrase '{phrase}' in training file '{tf.name}'!")
                leak_count += 1

    print(f"Test-only reserved phrases found in train data: {leak_count}")
    assert leak_count == 0, f"Found {leak_count} test reserved phrases in train data!"
    print("PASS: Train and test paraphrase pools are 100% disjoint, and test reserved phrases never appear in training data.")

if __name__ == "__main__":
    main()
