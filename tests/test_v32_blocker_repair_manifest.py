#!/usr/bin/env python3
"""
tests/test_v32_blocker_repair_manifest.py
Verifies presence, completeness, and non-empty status of all deliverables
for V32-MASTER-001 Blocker Repair.
"""

import os
import csv
import pytest

WORKSPACE = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
REPORT_DIR = os.path.join(WORKSPACE, "reports", "v32_blocker_repair_001")

DELIVERABLES = [
    "BACKUP_GAP_ANALYSIS.md",
    "VPS_ACCESS_AND_SNAPSHOT_PLAN.md",
    "REAL_ORDER_VERIFICATION.md",
    "ORIGINAL_V32_COMPONENT_MAP.md",
    "NEWS_CORE_AND_AKPP_AUDIT.md",
    "SHADOW_IMPLEMENTATION_STATUS.md",
    "CORRECTED_READINESS_MATRIX.csv",
    "FINAL_BLOCKER_REPORT.md",
]

def test_all_deliverables_exist():
    for d in DELIVERABLES:
        p = os.path.join(REPORT_DIR, d)
        assert os.path.exists(p), f"Missing deliverable: {p}"
        assert os.path.getsize(p) > 200, f"Deliverable too small or empty: {p}"

def test_corrected_readiness_matrix_blocked_status():
    csv_p = os.path.join(REPORT_DIR, "CORRECTED_READINESS_MATRIX.csv")
    with open(csv_p, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    assert len(reader) == 10, f"Expected 10 gates, found {len(reader)}"

    gate_map = {r["component_id"]: r for r in reader}
    # Verify VPS-dependent gates are BLOCKED
    assert gate_map["G-01_VPS_DISK_BACKUP"]["stage_status"] == "BLOCKED"
    assert gate_map["G-02_V32_PRE_RESET_ARCHIVE"]["stage_status"] == "BLOCKED"
    assert gate_map["G-03_REAL_ORDER_CHECK"]["stage_status"] == "BLOCKED"
    assert gate_map["G-04_SAFE_STOP_EXECUTION"]["stage_status"] == "BLOCKED"
    assert gate_map["G-10_SYNCHRONIZED_T0_START"]["stage_status"] == "BLOCKED"

    # Verify locally implemented/tested components
    assert gate_map["G-05_SHADOW_ENGINE_CORE"]["stage_status"] == "TESTED"
    assert gate_map["G-06_SHADOW_SQLITE_LEDGER"]["stage_status"] == "TESTED"
    assert gate_map["G-07_SHADOW_WEB_DASHBOARD"]["stage_status"] == "TESTED"

def test_backup_gap_analysis_content():
    p = os.path.join(REPORT_DIR, "BACKUP_GAP_ANALYSIS.md")
    txt = open(p, "r", encoding="utf-8").read()
    assert "LOCAL_SOURCE_BACKUP" in txt
    assert "LIVE_VPS_STATE_BACKUP" in txt
    assert "BLOCKED_NO_VPS_ACCESS" in txt
    assert "LIVE_VPS_STATE_NOT_CAPTURED" in txt

def test_real_order_verification_content():
    p = os.path.join(REPORT_DIR, "REAL_ORDER_VERIFICATION.md")
    txt = open(p, "r", encoding="utf-8").read()
    assert "NOT_VERIFIED" in txt
    assert "STOPPING_BLOCKED" in txt

def test_news_core_and_akpp_audit_content():
    p = os.path.join(REPORT_DIR, "NEWS_CORE_AND_AKPP_AUDIT.md")
    txt = open(p, "r", encoding="utf-8").read()
    assert "News Core v1.2" in txt
    assert "G1" in txt
    assert "61" in txt
    assert "АКПП" in txt
    assert "KellyRiskManager" in txt
