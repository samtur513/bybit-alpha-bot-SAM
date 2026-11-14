#!/usr/bin/env python3
"""
tests/test_v32_shadow_integrity_manifest.py
Validates the presence and integrity of all V32-SHADOW-INTEGRITY-002 deliverables.
"""

from pathlib import Path
import csv
import pytest

REPORTS_DIR = Path("reports/v32_shadow_integrity_002")

EXPECTED_FILES = [
    "SHADOW_PARITY_REPORT.md",
    "EXECUTION_AND_ACCOUNTING_REPORT.md",
    "BACKUP_AND_RECOVERY_SAFETY.md",
    "READINESS_MATRIX.csv",
]


def test_deliverables_existence():
    """Verifies that all required deliverable documents exist and are non-empty."""
    for filename in EXPECTED_FILES:
        filepath = REPORTS_DIR / filename
        assert filepath.exists(), f"Missing required deliverable: {filepath}"
        assert filepath.stat().st_size > 500, f"Deliverable is suspiciously small: {filepath}"


def test_readiness_matrix_taxonomy():
    """Verifies that the readiness matrix uses the strict 5-tier taxonomy and marks blocked gates."""
    matrix_file = REPORTS_DIR / "READINESS_MATRIX.csv"
    assert matrix_file.exists()

    allowed_statuses = {"DESIGNED", "IMPLEMENTED", "TESTED", "DEPLOYED", "BLOCKED"}
    rows = []
    with open(matrix_file, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
            assert row["Current_Status"] in allowed_statuses, f"Invalid status: {row['Current_Status']}"

    gate_map = {r["Gate_ID"]: r["Current_Status"] for r in rows}
    assert gate_map["G-01"] == "BLOCKED"
    assert gate_map["G-02"] == "BLOCKED"
    assert gate_map["G-03"] == "BLOCKED"
    assert gate_map["G-04"] == "BLOCKED"
    assert gate_map["G-07"] == "TESTED"
    assert gate_map["G-08"] == "TESTED"
    assert gate_map["G-09"] == "TESTED"
    assert gate_map["G-10"] == "BLOCKED"


def test_backup_and_recovery_safety_content():
    """Verifies critical safety revocations in BACKUP_AND_RECOVERY_SAFETY.md."""
    safety_file = REPORTS_DIR / "BACKUP_AND_RECOVERY_SAFETY.md"
    content = safety_file.read_text(encoding="utf-8")

    assert "create_vps_atomic_snapshot.sh" in content
    assert "PRAGMA wal_checkpoint(TRUNCATE)" in content
    assert "UNVERIFIED" in content
    assert "321.88" in content
    assert "120" in content
    assert "CANDLE_INTERVAL" in content
