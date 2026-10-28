"""Audit Check 1: Verify whether any file in agent/ was modified or created compared to pre-Phase-2 zip archive."""

import hashlib
from pathlib import Path
import zipfile

def get_file_sha256(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def main():
    agent_dir = Path("agent")
    zip_path = Path("D:/Computer_networks_proj.zip")

    current_agent_files = {p.relative_to(Path(".")).as_posix(): p for p in agent_dir.rglob("*") if p.is_file() and not p.name.endswith(".pyc") and "__pycache__" not in p.parts}

    print(f"Current files in agent/: {len(current_agent_files)}")
    for k in sorted(current_agent_files.keys()):
        print(f"  {k} ({get_file_sha256(current_agent_files[k])[:16]}...)")

    if not zip_path.is_file():
        print(f"WARNING: Zip archive {zip_path} not found.")
        return

    with zipfile.ZipFile(zip_path, 'r') as zf:
        zip_agent_files = {}
        for zi in zf.infolist():
            # handle both "Computer_networks_proj/agent/..." and "agent/..."
            p = zi.filename
            if p.startswith("Computer_networks_proj/"):
                p = p[len("Computer_networks_proj/"):]
            if p.startswith("agent/") and not zi.is_dir() and not p.endswith(".pyc") and "__pycache__" not in p:
                content = zf.read(zi)
                sha = hashlib.sha256(content).hexdigest()
                zip_agent_files[p] = sha

    print(f"\nFiles in pre-Phase-2 zip agent/: {len(zip_agent_files)}")
    
    diffs = []
    added = set(current_agent_files.keys()) - set(zip_agent_files.keys())
    removed = set(zip_agent_files.keys()) - set(current_agent_files.keys())

    for f in added:
        diffs.append(f"ADDED: {f}")
    for f in removed:
        diffs.append(f"REMOVED: {f}")

    for f in sorted(set(current_agent_files.keys()) & set(zip_agent_files.keys())):
        curr_sha = get_file_sha256(current_agent_files[f])
        zip_sha = zip_agent_files[f]
        if curr_sha != zip_sha:
            diffs.append(f"MODIFIED: {f} (curr: {curr_sha[:16]} != zip: {zip_sha[:16]})")
        else:
            print(f"  MATCH: {f}")

    print("\n=== Agent Audit Summary ===")
    if not diffs:
        print("PASS: Exact byte-for-byte match across all files in agent/. Zero files added, removed, or modified.")
    else:
        print(f"FAIL: Found {len(diffs)} differences:")
        for d in diffs:
            print(f"  {d}")

if __name__ == "__main__":
    main()
