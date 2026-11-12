"""
Data Integrity & Hash Manifest Generator
File: tools/compute_fleet_hashes.py
"""

import hashlib
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

def sha256_file(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def main():
    out_lines = [
        "# FLEET REBOOT DATA & ARTIFACT INTEGRITY MANIFEST (SHA-256)",
        "",
        "## 1. Input Historical Market Data (data/parquet_2024_2026/)",
        "",
        "| File Name | File Size (Bytes) | SHA-256 Checksum |",
        "| :--- | :--- | :--- |",
    ]

    parquet_dir = REPO_ROOT / "data" / "parquet_2024_2026"
    for p in sorted(parquet_dir.glob("*.parquet")):
        sz = p.stat().st_size
        h = sha256_file(p)
        out_lines.append(f"| `{p.name}` | {sz:,} | `{h}` |")

    out_lines.extend([
        "",
        "## 2. Output Replay & Paper V2 Pilot Artifacts (reports/fleet_reboot/)",
        "",
        "| File Name | File Size (Bytes) | SHA-256 Checksum |",
        "| :--- | :--- | :--- |",
    ])

    reboot_dir = REPO_ROOT / "reports" / "fleet_reboot"
    for f in sorted(reboot_dir.glob("*.*")):
        sz = f.stat().st_size
        h = sha256_file(f)
        out_lines.append(f"| `{f.name}` | {sz:,} | `{h}` |")

    dest = reboot_dir / "DATA_HASHES.md"
    dest.write_text("\n".join(out_lines), encoding="utf-8")
    print(f"Generated {dest} successfully.")

if __name__ == "__main__":
    main()
