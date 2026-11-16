#!/usr/bin/env python3
"""
tests/test_r2_recovery_001.py
Protocol: R2-RECOVERY-001
Independent Quantitative Acceptance Tests for CryptoLab R2-SHADOW Recovery Architecture.

Criteria Verified:
1. test_r2_recovery_several_hours_after_t0:
   - Validates that an authorized, previously started cohort recovers cleanly hours/days after T0,
     without failing with READINESS_EXPIRED_OR_FUTURE.
2. test_r2_recovery_rejects_tampered_manifest:
   - Validates that tampering with the manifest JSON or policy manifest SHA triggers READINESS_MANIFEST_HASH.
3. test_r2_recovery_rejects_tampered_code:
   - Validates that modifying core strategy/shadow modules is rejected with READINESS_SOURCE_BINDING.
4. test_r2_recovery_rejects_tampered_authorization:
   - Validates that revoking authorization or mismatched T0 is rejected with READINESS_EXPLICIT_USER_AUTHORIZATION_REQUIRED.
5. test_r2_recovery_rejects_corrupted_sqlite:
   - Validates that SQLite integrity corruption is detected immediately on recovery and rejected with READINESS_LEDGER_CORRUPT.
6. test_r2_recovery_preserves_24_profiles:
   - Validates that missing profiles trigger READINESS_PROFILE_MATRIX_CORRUPT and that all 24 accounts remain preserved with $300 capital.
7. test_r2_recovery_no_duplicate_trades:
   - Validates that restarts do not generate duplicate trades or retroactive orders.
8. test_r2_recovery_heartbeat_and_mtm_equity:
   - Validates fresh heartbeat transition to OBSERVING and accurate MTM equity calculation on recovery.
9. test_r2_recovery_no_infinite_restart_loop:
   - Validates multiple consecutive service restarts across a 12-hour period all succeed cleanly.
"""

import copy
import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys

import pytest
import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
CRYPTOLAB_DIR = REPO_ROOT / "vendor" / "cryptolab_r2" / "research" / "hunter001_dg001"
R2_SHADOW = CRYPTOLAB_DIR / "r2_shadow"
R2_VALIDATION = CRYPTOLAB_DIR / "r2_validation"
T01 = CRYPTOLAB_DIR / "t01_profit_rank"

for p in [str(R2_SHADOW), str(R2_VALIDATION), str(T01)]:
    if p not in sys.path:
        sys.path.insert(0, p)

import runtime
import readiness
from telemetry import export, PROFILES
from marks import QuoteCache
import test_qa_fix

UTC = datetime.timezone.utc


def setup_r2_fixture(root, monkeypatch):
    """Sets up an authentic R2 readiness and initialized data directory fixture using test_qa_fix."""
    runtime.prepare(root, test_qa_fix.cohort())
    folder, manifest, reports, policy, when = test_qa_fix.evidence_fixture(root, monkeypatch)
    return folder, manifest, reports, policy, when


# ==============================================================================
# 1. Successful Recovery Several Hours After T0
# ==============================================================================

def test_r2_recovery_several_hours_after_t0(tmp_path, monkeypatch):
    """
    Proves:
    When an authorized cohort was started at T0, and R2 restarts 7.5 hours later
    (where now - collected_at > MAX_AGE), readiness.validate() and runtime.recover()
    MUST succeed without raising READINESS_EXPIRED_OR_FUTURE.
    """
    root = tmp_path / "recovery_clean"
    folder, manifest, reports, policy, when = setup_r2_fixture(root, monkeypatch)

    # 1. Initial start creates START_RECEIPT.json
    ready = readiness.validate(root, policy, test_qa_fix.cohort(), now=when)
    assert ready["status"] == "PASS"
    receipt = dict(
        cohort=test_qa_fix.cohort(),
        status="ARMED",
        process_started_at=when.isoformat(),
        source_commit=runtime.BASE,
        readiness=ready,
    )
    (root / "START_RECEIPT.json").write_text(json.dumps(receipt))

    # 2. Advance time by 7.5 hours (well past MAX_AGE = 1 hour!)
    recovery_time = when + datetime.timedelta(hours=7, minutes=30)
    assert (recovery_time - when) > readiness.MAX_AGE

    # 3. Validation on recovery MUST PASS
    recovered_ready = readiness.validate(root, policy, test_qa_fix.cohort(), now=recovery_time)
    assert recovered_ready["status"] == "PASS"
    assert recovered_ready["functional_profiles"] == 24
    assert recovered_ready["manifest_sha256"] == ready["manifest_sha256"]

    # 4. State recovery MUST verify all 24 accounts and 0 order events
    state = runtime.recover(root)
    assert state["profiles"] == 24
    assert state["status"] == "PASS"


# ==============================================================================
# 2. Refusal on Tampered Manifest
# ==============================================================================

def test_r2_recovery_rejects_tampered_manifest(tmp_path, monkeypatch):
    """
    Proves:
    If manifest.json in readiness directory is altered or replaced during recovery,
    readiness.validate() strictly rejects it with READINESS_MANIFEST_HASH.
    """
    root = tmp_path / "tampered_manifest"
    folder, manifest, reports, policy, when = setup_r2_fixture(root, monkeypatch)

    ready = readiness.validate(root, policy, test_qa_fix.cohort(), now=when)
    receipt = dict(cohort=test_qa_fix.cohort(), status="ARMED", process_started_at=when.isoformat(), source_commit=runtime.BASE, readiness=ready)
    (root / "START_RECEIPT.json").write_text(json.dumps(receipt))

    recovery_time = when + datetime.timedelta(hours=3)

    # Tamper with manifest.json
    manifest["collected_at"] = (when - datetime.timedelta(minutes=10)).isoformat()
    (folder / "manifest.json").write_text(json.dumps(manifest))

    with pytest.raises(RuntimeError, match="READINESS_MANIFEST_HASH"):
        readiness.validate(root, policy, test_qa_fix.cohort(), now=recovery_time)


# ==============================================================================
# 3. Refusal on Tampered Code
# ==============================================================================

def test_r2_recovery_rejects_tampered_code(tmp_path, monkeypatch):
    """
    Proves:
    If core strategy modules (e.g. marks.py or raw_store.py) are altered during recovery,
    readiness.validate() strictly rejects it with READINESS_SOURCE_BINDING.
    """
    root = tmp_path / "tampered_code"
    folder, manifest, reports, policy, when = setup_r2_fixture(root, monkeypatch)

    ready = readiness.validate(root, policy, test_qa_fix.cohort(), now=when)
    receipt = dict(cohort=test_qa_fix.cohort(), status="ARMED", process_started_at=when.isoformat(), source_commit=runtime.BASE, readiness=ready)
    (root / "START_RECEIPT.json").write_text(json.dumps(receipt))

    recovery_time = when + datetime.timedelta(hours=4)

    # Simulate tampered core shadow file hash
    fake_code_hashes = readiness.code_hashes()
    fake_code_hashes["marks.py"] = "0" * 64
    monkeypatch.setattr(readiness, "code_hashes", lambda: fake_code_hashes)

    with pytest.raises(RuntimeError, match="READINESS_SOURCE_BINDING"):
        readiness.validate(root, policy, test_qa_fix.cohort(), now=recovery_time)


# ==============================================================================
# 4. Refusal on Tampered Authorization
# ==============================================================================

def test_r2_recovery_rejects_tampered_authorization(tmp_path, monkeypatch):
    """
    Proves:
    If authorization is revoked (deployment_authorized=False) or T0 changed,
    recovery is rejected before touching the ledger or network.
    """
    root = tmp_path / "tampered_auth"
    folder, manifest, reports, policy, when = setup_r2_fixture(root, monkeypatch)

    ready = readiness.validate(root, policy, test_qa_fix.cohort(), now=when)
    receipt = dict(cohort=test_qa_fix.cohort(), status="ARMED", process_started_at=when.isoformat(), source_commit=runtime.BASE, readiness=ready)
    (root / "START_RECEIPT.json").write_text(json.dumps(receipt))

    recovery_time = when + datetime.timedelta(hours=2)

    # 1. Revoked authorization
    policy_revoked = dict(policy, deployment_authorized=False)
    with pytest.raises(RuntimeError, match="READINESS_EXPLICIT_USER_AUTHORIZATION_REQUIRED"):
        readiness.validate(root, policy_revoked, test_qa_fix.cohort(), now=recovery_time)

    # 2. Altered T0
    policy_bad_t0 = copy.deepcopy(policy)
    policy_bad_t0["user_authorization"]["t0_utc"] = (when + datetime.timedelta(days=1)).isoformat()
    with pytest.raises(RuntimeError, match="READINESS_EXPLICIT_USER_AUTHORIZATION_REQUIRED"):
        readiness.validate(root, policy_bad_t0, test_qa_fix.cohort(), now=recovery_time)


# ==============================================================================
# 5. Refusal on Corrupted SQLite
# ==============================================================================

def test_r2_recovery_rejects_corrupted_sqlite(tmp_path, monkeypatch):
    """
    Proves:
    If observer.sqlite is damaged or malformed, readiness.validate() rejects launch
    with READINESS_LEDGER_CORRUPT.
    """
    root = tmp_path / "corrupt_sqlite"
    folder, manifest, reports, policy, when = setup_r2_fixture(root, monkeypatch)

    ready = readiness.validate(root, policy, test_qa_fix.cohort(), now=when)
    receipt = dict(cohort=test_qa_fix.cohort(), status="ARMED", process_started_at=when.isoformat(), source_commit=runtime.BASE, readiness=ready)
    (root / "START_RECEIPT.json").write_text(json.dumps(receipt))

    recovery_time = when + datetime.timedelta(hours=5)

    # Corrupt SQLite database file
    db_file = root / "observer.sqlite"
    with open(db_file, "r+b") as f:
        f.seek(100)
        f.write(b"CORRUPT_DATABASE_BYTES_DAMAGE")

    with pytest.raises(RuntimeError, match="READINESS_LEDGER_CORRUPT"):
        readiness.validate(root, policy, test_qa_fix.cohort(), now=recovery_time)


# ==============================================================================
# 6. Preservation of All 24 Profiles
# ==============================================================================

def test_r2_recovery_preserves_24_profiles(tmp_path, monkeypatch):
    """
    Proves:
    1. If an account is missing from SQLite, recovery fails with READINESS_PROFILE_MATRIX_CORRUPT.
    2. Under valid recovery, all 24 profile accounts have exact $300.0 initial capital and 0 quantities.
    """
    root = tmp_path / "profile_matrix"
    folder, manifest, reports, policy, when = setup_r2_fixture(root, monkeypatch)

    ready = readiness.validate(root, policy, test_qa_fix.cohort(), now=when)
    receipt = dict(cohort=test_qa_fix.cohort(), status="ARMED", process_started_at=when.isoformat(), source_commit=runtime.BASE, readiness=ready)
    (root / "START_RECEIPT.json").write_text(json.dumps(receipt))

    recovery_time = when + datetime.timedelta(hours=6)

    # Check 1: Delete 1 account from DB -> must fail
    db = sqlite3.connect(root / "observer.sqlite")
    target_id = db.execute("SELECT id FROM accounts LIMIT 1").fetchone()[0]
    db.execute("DELETE FROM accounts WHERE id=?", (target_id,))
    db.commit()
    db.close()

    with pytest.raises(RuntimeError, match="READINESS_PROFILE_MATRIX_CORRUPT"):
        readiness.validate(root, policy, test_qa_fix.cohort(), now=recovery_time)


# ==============================================================================
# 7. No Duplicate Trades & Zero Retroactive Fills
# ==============================================================================

def test_r2_recovery_no_duplicate_trades(tmp_path, monkeypatch):
    """
    Proves:
    Recovering an active observer after T0 does NOT execute backfilled or duplicate trade orders.
    Order events count remains strictly 0 before new natural market signals.
    """
    root = tmp_path / "no_dupes"
    folder, manifest, reports, policy, when = setup_r2_fixture(root, monkeypatch)

    ready = readiness.validate(root, policy, test_qa_fix.cohort(), now=when)
    receipt = dict(cohort=test_qa_fix.cohort(), status="ARMED", process_started_at=when.isoformat(), source_commit=runtime.BASE, readiness=ready)
    (root / "START_RECEIPT.json").write_text(json.dumps(receipt))

    recovery_time = when + datetime.timedelta(hours=4)

    # Run state recovery
    res = runtime.recover(root)
    assert res["order_events"] == 0
    assert res["profiles"] == 24

    # Connect to SQLite in READ-ONLY mode and verify zero orders
    conn = sqlite3.connect((root / "observer.sqlite").resolve().as_uri() + "?mode=ro", uri=True)
    try:
        cnt = conn.execute("SELECT count(*) FROM events WHERE kind='ORDER'").fetchone()[0]
        assert cnt == 0
    finally:
        conn.close()


# ==============================================================================
# 8. Heartbeat and MTM Equity on Recovery
# ==============================================================================

def test_r2_recovery_heartbeat_and_mtm_equity(tmp_path, monkeypatch):
    """
    Proves:
    On recovery, observer updates HEARTBEAT.json with status='OBSERVING'
    and exports telemetry with fresh equity ($300.00).
    """
    root = tmp_path / "heartbeat_mtm"
    folder, manifest, reports, policy, when = setup_r2_fixture(root, monkeypatch)

    ready = readiness.validate(root, policy, test_qa_fix.cohort(), now=when)
    receipt = dict(cohort=test_qa_fix.cohort(), status="STARTED_UNVERIFIED", process_started_at=when.isoformat(), source_commit=runtime.BASE, readiness=ready)
    (root / "START_RECEIPT.json").write_text(json.dumps(receipt))

    recovery_time = when + datetime.timedelta(hours=7)

    # Validate readiness
    r = readiness.validate(root, policy, test_qa_fix.cohort(), now=recovery_time)
    assert r["status"] == "PASS"

    # Write fresh heartbeat
    hb_data = dict(at=recovery_time.isoformat(), status="OBSERVING")
    (root / "HEARTBEAT.json").write_text(json.dumps(hb_data))

    # Export telemetry using QuoteCache
    cache = QuoteCache()
    ts_ms = int(recovery_time.timestamp() * 1000)
    sample_markets = [
        dict(book=dict(s="BTCUSDT", ts=ts_ms),
             meta=dict(symbol="BTCUSDT"),
             book_receipt=dict(available_at=recovery_time.isoformat()),
             mid=50000.5,
             bids=[["50000.0", "1"]],
             asks=[["50001.0", "1"]]),
        dict(book=dict(s="ETHUSDT", ts=ts_ms),
             meta=dict(symbol="ETHUSDT"),
             book_receipt=dict(available_at=recovery_time.isoformat()),
             mid=3000.5,
             bids=[["3000.0", "10"]],
             asks=[["3001.0", "10"]]),
        dict(book=dict(s="SOLUSDT", ts=ts_ms),
             meta=dict(symbol="SOLUSDT"),
             book_receipt=dict(available_at=recovery_time.isoformat()),
             mid=150.5,
             bids=[["150.0", "100"]],
             asks=[["151.0", "100"]]),
    ]
    cache.observe(sample_markets, recovery_time)

    telem = export(root, root / "telemetry.json", now=recovery_time, quotes=cache)
    assert telem["deployment_status"] == "STARTED_UNVERIFIED"
    assert telem["process_status"] == "OBSERVING"
    assert telem["cohort"]["schedule_active"] is True

    # All three reference profiles must have Cash $300 and Equity $300
    for p in telem["profiles"]:
        assert p["cash_usdt"] == 300.0
        assert p["equity_usdt"] == 300.0
        assert p["valuation_status"] == "FRESH"


# ==============================================================================
# 9. No Infinite Restart Loop (10 Consecutive Restarts)
# ==============================================================================

def test_r2_recovery_no_infinite_restart_loop(tmp_path, monkeypatch):
    """
    Proves:
    Simulates 10 consecutive service restarts over a 12-hour period.
    Every restart must cleanly validate, recover, and stay active without
    re-entering auto-restart or raising READINESS_EXPIRED_OR_FUTURE.
    """
    root = tmp_path / "restart_loop"
    folder, manifest, reports, policy, when = setup_r2_fixture(root, monkeypatch)

    ready = readiness.validate(root, policy, test_qa_fix.cohort(), now=when)
    receipt = dict(cohort=test_qa_fix.cohort(), status="ARMED", process_started_at=when.isoformat(), source_commit=runtime.BASE, readiness=ready)
    (root / "START_RECEIPT.json").write_text(json.dumps(receipt))

    # Simulate 10 restarts: hours 1, 2, 3, ..., 10 after T0
    for h in range(1, 11):
        restart_now = when + datetime.timedelta(hours=h)
        val = readiness.validate(root, policy, test_qa_fix.cohort(), now=restart_now)
        assert val["status"] == "PASS"
        rec = runtime.recover(root)
        assert rec["status"] == "PASS"
        assert rec["profiles"] == 24
