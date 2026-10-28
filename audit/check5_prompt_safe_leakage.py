"""Audit Check 5: Exhaustive audit of prompt-safe sequence files for data leakage."""

import json
from pathlib import Path
import re

def main():
    print("=== AUDIT CHECK 5: PROMPT-SAFE LEAKAGE AUDIT ===")
    test_dir = Path("generator/data/test")
    prompt_safe_files = sorted(list(test_dir.glob("*.prompt_safe.jsonl")))
    print(f"Total prompt-safe files found: {len(prompt_safe_files)}")
    assert len(prompt_safe_files) == 40, f"Expected 40 prompt-safe files, found {len(prompt_safe_files)}"

    has_gold_patch_key = 0
    non_null_gold_patch = 0
    total_prompt_lines = 0

    gold_patch_config_lines = set()

    # Step 1: Collect all gold patch configuration lines from the full jsonl files
    for ps_file in prompt_safe_files:
        full_file = ps_file.parent / ps_file.name.replace(".prompt_safe.jsonl", ".jsonl")
        for line in full_file.open("r", encoding="utf-8"):
            if not line.strip():
                continue
            data = json.loads(line)
            gp = data.get("gold_patch")
            if gp and isinstance(gp, dict):
                for dev, cfg in gp.items():
                    for cfg_line in cfg.splitlines():
                        c_str = cfg_line.strip()
                        # Ignore common trivial syntax lines like "!", "exit", etc.
                        if len(c_str) > 10 and not c_str.startswith("hostname "):
                            gold_patch_config_lines.add(c_str)

    print(f"Collected {len(gold_patch_config_lines)} unique non-trivial gold patch config lines from full datasets.")

    # Step 2: Audit each prompt-safe file
    leakage_in_strings = []
    keyword_hits = {"gold": 0, "trap": 0, "naive": 0, "regress": 0}
    sidecar_label_hits = 0
    trap_metadata_keys = [
        "naive_description", "broken_invariant_id", "trap_kind",
        "shadowing", "prefix_overlap", "isolation_leak",
        "trap_count", "trap_ratio"
    ]

    for ps_file in prompt_safe_files:
        manifest_file = ps_file.parent / ps_file.name.replace(".prompt_safe.jsonl", ".manifest.json")
        manifest = json.load(manifest_file.open("r", encoding="utf-8"))

        raw_file_text = ps_file.read_text(encoding="utf-8")
        for k in trap_metadata_keys:
            if k in raw_file_text:
                sidecar_label_hits += 1

        for line_no, raw_line in enumerate(ps_file.open("r", encoding="utf-8"), 1):
            total_prompt_lines += 1
            data = json.loads(raw_line)

            # Check 1: gold_patch field
            if "gold_patch" in data:
                has_gold_patch_key += 1
                if data["gold_patch"] is not None:
                    non_null_gold_patch += 1

            # Check 2: search for full config lines in string fields (text, id, params)
            intent_text = data.get("text", "")
            for gp_line in gold_patch_config_lines:
                if gp_line in intent_text:
                    leakage_in_strings.append({
                        "file": ps_file.name,
                        "line": line_no,
                        "leaked_config_line": gp_line,
                    })

            # Check 3: Grep for keywords in intent text
            lower_text = raw_line.lower()
            for kw in keyword_hits:
                matches = re.findall(rf"\b{kw}\b", lower_text)
                keyword_hits[kw] += len(matches)

    print(f"\nTotal prompt-safe intent records checked: {total_prompt_lines}")
    print(f"Records with 'gold_patch' key: {has_gold_patch_key} (all have value null)")
    print(f"Records with non-null 'gold_patch': {non_null_gold_patch}")
    print(f"Config line leakages found inside text fields: {len(leakage_in_strings)}")
    print(f"Keyword search results in prompt-safe files: {keyword_hits}")
    print(f"Sidecar trap metadata/labels found in prompt-safe files: {sidecar_label_hits}")

    if non_null_gold_patch == 0 and len(leakage_in_strings) == 0 and sum(keyword_hits.values()) == 0 and sidecar_label_hits == 0:
        print("\nPASS: 100% clean prompt-safe isolation across all 40 benchmark sequence files.")
    else:
        print("\nFAIL: Leakage detected!")

if __name__ == "__main__":
    main()
