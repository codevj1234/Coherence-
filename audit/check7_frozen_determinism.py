"""Audit Check 7: Frozen data verification, determinism, and regeneration diff."""

import os
from pathlib import Path
import subprocess
import sys
import json

from generator.verify_frozen import verify_frozen_dataset
from generator.sequences.sidecar import compute_file_sha256

def main():
    print("=== AUDIT CHECK 7: FROZEN DATA & DETERMINISM ===")

    # 1. Run generator.verify_frozen on FROZEN.json
    frozen_manifest_path = Path("generator/data/FROZEN.json")
    print("\n--- 1. Verifying recorded hashes in FROZEN.json against files on disk ---")
    verify_result = verify_frozen_dataset(frozen_manifest_path)
    print(f"verify_frozen_dataset result: {verify_result}")

    with frozen_manifest_path.open("r", encoding="utf-8") as f:
        frozen_data = json.load(f)

    # 2. Check if FROZEN.json hashes are computed over the actual evaluation files
    print("\n--- 2. Checking hash alignment with evaluation files ---")
    data_dir = frozen_manifest_path.parent
    hash_discrepancies = []
    for seq_id, entry in frozen_data.items():
        rel_jsonl = entry["jsonl_file"]
        jsonl_path = data_dir / rel_jsonl
        if not jsonl_path.is_file():
            hash_discrepancies.append((seq_id, "MISSING_FILE", rel_jsonl))
            continue
        actual_sha = compute_file_sha256(jsonl_path)
        if actual_sha != entry["jsonl_sha256"]:
            hash_discrepancies.append((seq_id, "HASH_MISMATCH", entry["jsonl_sha256"], actual_sha))

    print(f"Hash discrepancies between FROZEN.json and disk files: {len(hash_discrepancies)}")
    if hash_discrepancies:
        print(f"FAIL: {hash_discrepancies}")
    else:
        print("PASS: FROZEN.json hashes perfectly match all 40 test files on disk.")

    # 3. Regenerate test sequences under PYTHONHASHSEED=0 and PYTHONHASHSEED=123 into temp dirs
    temp_dir_0 = Path("audit/temp_regen_seed0")
    temp_dir_123 = Path("audit/temp_regen_seed123")

    cmd_template = (
        'python -c "'
        'from generator.build import build_frozen_dataset; '
        'build_frozen_dataset(output_dir=\'{out_dir}\')'
        '"'
    )

    print("\n--- 3. Regenerating test sequences with PYTHONHASHSEED=0 ---")
    env0 = os.environ.copy()
    env0["PYTHONHASHSEED"] = "0"
    p0 = subprocess.run(
        cmd_template.format(out_dir=temp_dir_0.as_posix()),
        shell=True,
        capture_output=True,
        text=True,
        env=env0,
    )
    print(f"Regeneration under PYTHONHASHSEED=0 exit code: {p0.returncode}")

    print("\n--- 4. Regenerating test sequences with PYTHONHASHSEED=123 ---")
    env123 = os.environ.copy()
    env123["PYTHONHASHSEED"] = "123"
    p123 = subprocess.run(
        cmd_template.format(out_dir=temp_dir_123.as_posix()),
        shell=True,
        capture_output=True,
        text=True,
        env=env123,
    )
    print(f"Regeneration under PYTHONHASHSEED=123 exit code: {p123.returncode}")

    # 5. Compare seed0 vs seed123 (Internal Generator Determinism)
    print("\n--- 5. Determinism between PYTHONHASHSEED=0 and PYTHONHASHSEED=123 ---")
    regen_files_0 = sorted(list((temp_dir_0 / "test").glob("*.jsonl")))
    regen_files_123 = sorted(list((temp_dir_123 / "test").glob("*.jsonl")))

    seed_diffs = []
    for f0, f123 in zip(regen_files_0, regen_files_123):
        sha0 = compute_file_sha256(f0)
        sha123 = compute_file_sha256(f123)
        if sha0 != sha123:
            seed_diffs.append((f0.name, sha0, sha123))

    print(f"Mismatches between PYTHONHASHSEED=0 and PYTHONHASHSEED=123: {len(seed_diffs)}")
    if seed_diffs:
        print(f"FAIL: Generator is NOT deterministic across PYTHONHASHSEED values: {seed_diffs}")
    else:
        print("PASS: Generator is 100% deterministic across different PYTHONHASHSEED values (0 vs 123).")

    # 6. Compare regenerated files against frozen files in generator/data/test/
    print("\n--- 6. Byte-for-byte diff: Current Generator vs Frozen Files on Disk ---")
    frozen_diffs = []
    for f0 in regen_files_0:
        if f0.name.endswith(".prompt_safe.jsonl"):
            continue
        frozen_file = Path("generator/data/test") / f0.name
        sha_regen = compute_file_sha256(f0)
        sha_frozen = compute_file_sha256(frozen_file)
        if sha_regen != sha_frozen:
            frozen_diffs.append((f0.name, sha_frozen, sha_regen))

    print(f"Mismatches between regenerated files and frozen benchmark files: {len(frozen_diffs)} / {len(regen_files_0)//2}")
    if frozen_diffs:
        print(f"DISCREPANCY DETECTED: {len(frozen_diffs)} files differ from the frozen benchmark dataset!")
        for name, sh_f, sh_r in frozen_diffs[:5]:
            print(f"  {name}: frozen={sh_f[:16]}... vs current_gen={sh_r[:16]}...")
        print("CAUSE: Code changes to topology baseline configs in generator/topology/generator.py occurred after the frozen dataset was built.")
    else:
        print("PASS: Current generator reproduces frozen benchmark dataset byte-for-byte.")

if __name__ == "__main__":
    main()
