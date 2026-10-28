"""CLI driver for generating benchmark change sequences and frozen test sets."""

import argparse
import json
from pathlib import Path
import sys
from generator.sequences.builder import build_sequence, save_sequence_files
from generator.sequences.sidecar import compute_file_sha256
from generator.topology.catalog import TOPOLOGIES, get_topology, list_topologies


def build_frozen_dataset(output_dir: str | Path = "generator/data") -> dict[str, str]:
    """Generate the full frozen test benchmark dataset (at least 20 sequences per test topology) plus train set."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=== Generating Benchmark Sequence Dataset ===")
    test_topologies = list_topologies(split="test")
    train_topologies = list_topologies(split="train")

    frozen_manifest: dict[str, dict[str, str]] = {}

    # 1. Generate 20 test sequences for EACH test topology (total 40 test sequences)
    # Using length = 50 steps
    for topo in test_topologies:
        print(f"\nBuilding 20 test sequences for topology '{topo.id}' ({len(topo.nodes)} nodes)...")
        for seq_idx in range(1, 21):
            seed = 1000 + seq_idx * 7
            seq_id = f"{topo.id}_test_seq_{seq_idx:03d}"
            seq = build_sequence(
                spec=topo,
                length=50,
                seed=seed,
                sequence_id=seq_id,
                split="test",
            )
            jsonl_p, manifest_p, safe_p = save_sequence_files(seq, out_dir / "test")
            sha256 = compute_file_sha256(jsonl_p)
            safe_sha256 = compute_file_sha256(safe_p)

            frozen_manifest[seq_id] = {
                "sequence_id": seq_id,
                "topology_id": topo.id,
                "split": "test",
                "seed": seed,
                "length": seq.manifest.length,
                "traps": seq.manifest.trap_count,
                "trap_ratio": seq.manifest.trap_ratio,
                "jsonl_file": str(jsonl_p.relative_to(out_dir)).replace("\\", "/"),
                "jsonl_sha256": sha256,
                "prompt_safe_file": str(safe_p.relative_to(out_dir)).replace("\\", "/"),
                "prompt_safe_sha256": safe_sha256,
            }
            print(f"  [{seq_idx:02d}/20] {seq_id}: {seq.manifest.trap_count}/50 traps ({seq.manifest.trap_ratio:.1%}) - SHA: {sha256[:12]}...")

        # Add long evaluation sequences (length 100 and length 200) for test topology
        for long_len in [100, 200]:
            seed = 3000 + long_len
            seq_id = f"{topo.id}_test_seq_len{long_len}_001"
            print(f"  Building long test sequence {seq_id} (length={long_len}, seed={seed})...")
            seq = build_sequence(
                spec=topo,
                length=long_len,
                seed=seed,
                sequence_id=seq_id,
                split="test",
            )
            jsonl_p, manifest_p, safe_p = save_sequence_files(seq, out_dir / "test")
            sha256 = compute_file_sha256(jsonl_p)
            safe_sha256 = compute_file_sha256(safe_p)
            frozen_manifest[seq_id] = {
                "sequence_id": seq_id,
                "topology_id": topo.id,
                "split": "test",
                "seed": seed,
                "length": seq.manifest.length,
                "traps": seq.manifest.trap_count,
                "trap_ratio": seq.manifest.trap_ratio,
                "jsonl_file": str(jsonl_p.relative_to(out_dir)).replace("\\", "/"),
                "jsonl_sha256": sha256,
                "prompt_safe_file": str(safe_p.relative_to(out_dir)).replace("\\", "/"),
                "prompt_safe_sha256": safe_sha256,
            }
            print(f"  [LONG {long_len}] {seq_id}: {seq.manifest.trap_count}/{long_len} traps ({seq.manifest.trap_ratio:.1%}) - SHA: {sha256[:12]}...")

    # 2. Generate a modest train set (5 sequences per train topology)
    for topo in train_topologies:
        print(f"\nBuilding 5 train sequences for topology '{topo.id}' ({len(topo.nodes)} nodes)...")
        for seq_idx in range(1, 6):
            seed = 2000 + seq_idx * 11
            seq_id = f"{topo.id}_train_seq_{seq_idx:03d}"
            seq = build_sequence(
                spec=topo,
                length=50,
                seed=seed,
                sequence_id=seq_id,
                split="train",
            )
            jsonl_p, manifest_p, safe_p = save_sequence_files(seq, out_dir / "train")
            sha256 = compute_file_sha256(jsonl_p)
            print(f"  [{seq_idx:02d}/05] {seq_id}: {seq.manifest.trap_count}/50 traps ({seq.manifest.trap_ratio:.1%}) - SHA: {sha256[:12]}...")

    # 3. Write FROZEN.json
    frozen_path = out_dir / "FROZEN.json"
    frozen_json_content = json.dumps(frozen_manifest, sort_keys=True, indent=2) + "\n"
    frozen_path.write_text(frozen_json_content, encoding="utf-8")
    print(f"\nFROZEN manifest written to: {frozen_path} ({len(frozen_manifest)} test sequences frozen).")

    return {k: v["jsonl_sha256"] for k, v in frozen_manifest.items()}


def main() -> None:
    """CLI entrypoint for sequence builder."""
    parser = argparse.ArgumentParser(description="Coherence Under Change benchmark sequence generator.")
    parser.add_argument("--topology", help="Topology ID (e.g. topo_train_small, topo_test_large).")
    parser.add_argument("--split", choices=["train", "test"], help="Dataset split.")
    parser.add_argument("--n", type=int, default=1, help="Number of sequences to generate.")
    parser.add_argument("--length", type=int, default=50, help="Length of each sequence (50 to 200).")
    parser.add_argument("--seed", type=int, default=42, help="Starting random seed.")
    parser.add_argument("--out", default="generator/data", help="Output directory.")
    parser.add_argument("--bulk-frozen", action="store_true", help="Generate full frozen benchmark dataset.")

    args = parser.parse_args()

    if args.bulk_frozen:
        build_frozen_dataset(output_dir=args.out)
        return

    if not args.topology:
        print("Error: --topology is required unless --bulk-frozen is specified.", file=sys.stderr)
        sys.exit(1)

    spec = get_topology(args.topology)
    split = args.split or spec.split
    out_dir = Path(args.out)

    for i in range(args.n):
        current_seed = args.seed + i
        seq_id = f"{spec.id}_{split}_s{current_seed}"
        print(f"Generating sequence {seq_id} (length={args.length}, seed={current_seed})...")
        seq = build_sequence(
            spec=spec,
            length=args.length,
            seed=current_seed,
            sequence_id=seq_id,
            split=split,
        )
        jsonl_p, manifest_p, safe_p = save_sequence_files(seq, out_dir / split)
        print(f"Saved: {jsonl_p} (traps: {seq.manifest.trap_count}/{seq.manifest.length})")


if __name__ == "__main__":
    main()
