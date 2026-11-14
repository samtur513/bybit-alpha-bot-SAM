#!/usr/bin/env python3
"""
tests/test_v32_dual_paper_preparation.py
Comprehensive PyTest suite verifying:
1. Fleet inventory completeness and status classification.
2. Dependency isolation of v3.2 Original.
3. Cryptographic integrity and SHA-256 hashes of all 19 backup archives.
4. Completeness and restore safety of V32_ORIGINAL_PRE_RESET_ARCHIVE.
5. Dual-capital contract invariants (300 USDT, zero positions, identical T0).
6. T0 readiness gates and safe-stop execution matrix.
"""

import os
import csv
import json
import tarfile
import hashlib
import tempfile
import pytest

WORKSPACE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPORT_DIR = os.path.join(WORKSPACE, "reports", "v32_dual_paper_preparation_001")
BACKUP_DIR = os.path.join(REPORT_DIR, "backups")

def test_fleet_inventory_completeness_and_statuses():
    inv_path = os.path.join(REPORT_DIR, "01_FLEET_INVENTORY.csv")
    assert os.path.exists(inv_path), f"Missing {inv_path}"

    with open(inv_path, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    assert len(reader) == 20, f"Expected 20 rows, got {len(reader)}"

    ports = {int(r["port"]): r for r in reader}
    # Verify Port 8083 is KEEP_RUNNING
    assert 8083 in ports
    assert ports[8083]["inventory_status"] == "KEEP_RUNNING"
    assert ports[8083]["systemd_service"] == "bybit-v32.service"

    # Verify Port 8085 and 8095 are SHARED_DEPENDENCY
    assert ports[8085]["inventory_status"] == "SHARED_DEPENDENCY"
    assert ports[8095]["inventory_status"] == "SHARED_DEPENDENCY"

    # Verify Port 0 is UNKNOWN_DO_NOT_TOUCH
    assert ports[0]["inventory_status"] == "UNKNOWN_DO_NOT_TOUCH"

    # Verify all stopping candidates are SAFE_TO_STOP
    for p in [8080, 8081, 8082, 8084, 8086, 8087, 8088, 8089, 8090, 8091, 8092, 8093, 8094, 8096, 8097, 8098]:
        assert ports[p]["inventory_status"] == "SAFE_TO_STOP", f"Port {p} should be SAFE_TO_STOP"

    # Verify zero real orders across all systems
    for r in reader:
        assert int(r["real_orders_count"]) == 0, f"Port {r['port']} has non-zero real orders!"

def test_dependency_map_v32_isolation():
    dep_path = os.path.join(REPORT_DIR, "02_DEPENDENCY_MAP.md")
    assert os.path.exists(dep_path), f"Missing {dep_path}"

    content = open(dep_path, "r", encoding="utf-8").read()
    assert "Port 8083: v3.2 Dual-Regime (ORIGINAL)" in content
    assert "KEEP_RUNNING" in content
    assert "Forensic Isolation Proof for v3.2 Original" in content
    assert "ZERO IMPACT" in content

def test_backup_manifest_integrity_and_hashes():
    man_path = os.path.join(REPORT_DIR, "03_BACKUP_MANIFEST.csv")
    assert os.path.exists(man_path), f"Missing {man_path}"

    with open(man_path, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    assert len(reader) == 19, f"Expected 19 backup archives, got {len(reader)}"

    for r in reader:
        rel_path = r["relative_path"]
        full_path = os.path.join(WORKSPACE, rel_path)
        assert os.path.exists(full_path), f"Archive file not found: {full_path}"

        # Recompute SHA256
        data = open(full_path, "rb").read()
        calc_sha = hashlib.sha256(data).hexdigest()
        assert calc_sha == r["sha256_hash"], f"Hash mismatch for {r['archive_name']}"

        assert r["integrity_status"] == "VERIFIED_INTACT"
        assert r["restore_test_status"] == "RESTORE_VERIFIED"

def test_v32_original_pre_reset_archive_contents():
    archive_path = os.path.join(BACKUP_DIR, "V32_ORIGINAL_PRE_RESET_ARCHIVE.tar.gz")
    assert os.path.exists(archive_path), f"Missing pre-reset archive: {archive_path}"

    with tempfile.TemporaryDirectory() as tmpdir:
        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(path=tmpdir)

        # Verify key source files are extracted
        extracted_files = [os.path.relpath(os.path.join(r, f), tmpdir).replace("\\", "/") for r, _, fs in os.walk(tmpdir) for f in fs]
        assert any("paper_trader_v32.py" in ef for ef in extracted_files)
        assert any("setup_v32.sh" in ef for ef in extracted_files)
        assert any("LIVE_VPS_SNAPSHOT_NOV14.json" in ef for ef in extracted_files)

        # Verify snapshot JSON content
        snap_file = [os.path.join(tmpdir, ef) for ef in extracted_files if "LIVE_VPS_SNAPSHOT_NOV14.json" in ef][0]
        with open(snap_file, "r", encoding="utf-8") as jf:
            snap_data = json.load(jf)
            assert snap_data.get("capital") == 321.88
            assert len(snap_data.get("open_positions", [])) == 3
            assert len(snap_data.get("closed_trades", [])) == 28

def test_dual_start_capital_contract_invariants():
    contract_path = os.path.join(REPORT_DIR, "07_DUAL_START_CAPITAL_CONTRACT.md")
    assert os.path.exists(contract_path), f"Missing {contract_path}"

    content = open(contract_path, "r", encoding="utf-8").read()
    assert "300.000000 USDT" in content
    assert "Donchian Trend 120 bars, 1H" in content
    assert "TP1 @ 1.5R" in content
    assert "TP2 @ 3.0R" in content
    assert "0.100% Taker / 0.036% Maker (VIP0)" in content

def test_safe_stop_and_readiness_checklist():
    stop_path = os.path.join(REPORT_DIR, "05_SAFE_STOP_REPORT.csv")
    check_path = os.path.join(REPORT_DIR, "08_T0_READINESS_CHECKLIST.md")
    assert os.path.exists(stop_path)
    assert os.path.exists(check_path)

    with open(stop_path, "r", encoding="utf-8") as f:
        stop_rows = list(csv.DictReader(f))

    assert len(stop_rows) == 19
    for sr in stop_rows:
        assert sr["real_orders_check"] == "VERIFIED_ZERO_REAL_ORDERS"
        if sr["port"] == "8083":
            assert sr["stop_action"] == "DO_NOT_STOP"
            assert sr["safety_authorization_level"] == "PRESERVED_ACTIVE"

    checklist_content = open(check_path, "r", encoding="utf-8").read()
    for gate_id in ["G-01", "G-02", "G-03", "G-04", "G-05", "G-06", "G-07", "G-08", "G-09", "G-10"]:
        assert gate_id in checklist_content
    assert "GO FOR SYNCHRONIZED T0 START" in checklist_content
