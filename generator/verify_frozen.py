"""CLI verifier for the frozen benchmark test dataset."""

import argparse
import json
from pathlib import Path
import sys
from generator.sequences.sidecar import compute_file_sha256


def verify_frozen_dataset(frozen_manifest_path: str | Path = "generator/data/FROZEN.json") -> bool:
    """Verify that all frozen test sequence files exist and match recorded SHA-256 hashes exactly."""
    manifest_p = Path(frozen_manifest_path)
    if not manifest_p.is_file():
        print(f"Error: Frozen manifest not found at '{manifest_p}'. Run generator.build --bulk-frozen first.", file=sys.stderr)
        return False

    base_dir = manifest_p.parent

    with manifest_p.open("r", encoding="utf-8") as f:
        data = json.load(f)

    print(f"=== Verifying Frozen Benchmark Test Dataset ({len(data)} sequences) ===")
    all_ok = True
    checked_count = 0

    for seq_id, entry in sorted(data.items()):
        checked_count += 1
        rel_path = entry["jsonl_file"]
        expected_hash = entry["jsonl_sha256"]
        file_path = base_dir / rel_path

        if not file_path.is_file():
            print(f"  [MISSING] {seq_id}: File not found: {file_path}", file=sys.stderr)
            all_ok = False
            continue

        actual_hash = compute_file_sha256(file_path)
        if actual_hash != expected_hash:
            print(f"  [HASH MISMATCH] {seq_id}: Expected {expected_hash}, got {actual_hash}", file=sys.stderr)
            all_ok = False
            continue

        # Also verify prompt-safe file if present
        if "prompt_safe_file" in entry:
            safe_rel_path = entry["prompt_safe_file"]
            safe_expected_hash = entry.get("prompt_safe_sha256")
            safe_file_path = base_dir / safe_rel_path

            if not safe_file_path.is_file():
                print(f"  [MISSING PROMPT-SAFE] {seq_id}: {safe_file_path}", file=sys.stderr)
                all_ok = False
                continue

            if safe_expected_hash:
                actual_safe_hash = compute_file_sha256(safe_file_path)
                if actual_safe_hash != safe_expected_hash:
                    print(f"  [PROMPT-SAFE HASH MISMATCH] {seq_id}", file=sys.stderr)
                    all_ok = False
                    continue

        print(f"  [OK] {seq_id}: verified SHA-256 {actual_hash[:16]}...")

    if all_ok:
        print(f"\nSUCCESS: All {checked_count} frozen benchmark sequences verified byte-for-byte!")
    else:
        print(f"\nFAILURE: One or more frozen sequence files were missing or modified!", file=sys.stderr)

    return all_ok


def main() -> None:
    """CLI entrypoint for verify_frozen."""
    parser = argparse.ArgumentParser(description="Verify frozen benchmark test dataset integrity.")
    parser.add_argument(
        "--manifest",
        default="generator/data/FROZEN.json",
        help="Path to FROZEN.json manifest file (default: generator/data/FROZEN.json).",
    )
    args = parser.parse_args()

    ok = verify_frozen_dataset(args.manifest)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
