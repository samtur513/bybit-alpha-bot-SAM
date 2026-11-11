import hashlib
import os
import zipfile
from pathlib import Path

repo_root = Path(".").resolve()
archive_path_f1b = repo_root / ".scratch" / "f1b_audit_package_sanitized.zip"
archive_path_f1c = repo_root / ".scratch" / "f1c_audit_package_sanitized.zip"
archive_path_f1c_r2 = repo_root / ".scratch" / "f1c_r2_audit_package_sanitized.zip"

files_to_include = [
    # Repaired package
    "v3_5_repaired/__init__.py",
    "v3_5_repaired/src/__init__.py",
    "v3_5_repaired/src/config.py",
    "v3_5_repaired/src/alpha_pairs_engine.py",
    "v3_5_repaired/src/paper_trader_v35.py",
    "v3_5_repaired/src/financial_ledger.py",
    "v3_5_repaired/tests/__init__.py",
    "v3_5_repaired/tests/test_f1b_repaired_invariants.py",
    "v3_5_repaired/tests/test_lifecycle_integration.py",
    "v3_5_repaired/tests/test_mutation_audit.py",
    "v3_5_repaired/tests/test_production_mutation_audit.py",
    "v3_5_repaired/tests/test_f1c_r2_audit_suite.py",
    # Original test suite
    "tests/test_f1a_audit_invariants.py",
    # Repaired v3.8 trader (synthetic APT trade removed)
    "v3_8/src/paper_trader_v38.py",
]

for archive_path in [archive_path_f1b, archive_path_f1c, archive_path_f1c_r2]:
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel_path in files_to_include:
            full_path = repo_root / rel_path
            if full_path.exists():
                zf.write(full_path, arcname=rel_path)
                print(f"Added: {rel_path}")
            else:
                print(f"MISSING: {rel_path}")

    sha256 = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    print(f"\nArchive created: {archive_path}")
    print(f"Archive SHA-256: {sha256}")
    print(f"Archive Size: {archive_path.stat().st_size} bytes")
