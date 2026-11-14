"""
Verification Test Suite for V32-LEGACY-VERIFY-003.
Verifies price bounds corrections, Cascade TP 1-minute market sequence,
economic recalculation matrix, and Point-in-Time lifecycle invariants.
"""

import csv
import sys
from decimal import Decimal
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

CORRECTION_CSV = REPO_ROOT / "reports" / "v32_legacy_verify_003" / "01_PRICE_EVIDENCE_CORRECTION.csv"
CASCADE_CSV = REPO_ROOT / "reports" / "v32_legacy_verify_003" / "02_CASCADE_TP_EVENT_LEDGER.csv"
ECON_CSV = REPO_ROOT / "reports" / "v32_legacy_verify_003" / "04_ECONOMIC_RECALCULATION.csv"
LIFECYCLE_CSV = REPO_ROOT / "reports" / "v32_legacy_verify_003" / "05_PIT_LIFECYCLE_AUDIT.csv"
VERDICT_CSV = REPO_ROOT / "reports" / "v32_legacy_verify_003" / "06_FINAL_TRADE_VERDICT.csv"


def test_price_evidence_correction_strict_bounds():
    """
    Verifies that strict price bounds auditing without artificial tolerance identifies:
    - Exactly 26 trades strictly inside Bybit 1h [Low, High] bounds.
    - Exactly 2 trades (TR_03 NEAR and TR_06 XRP) as SLIPPAGE_OVERSHOOT_REJECT.
    """
    assert CORRECTION_CSV.exists(), f"Missing {CORRECTION_CSV}"
    with open(CORRECTION_CSV, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    assert len(reader) == 28, f"Expected 28 trades, got {len(reader)}"

    pass_trades = [r for r in reader if r["overall_strict_price_verdict"] == "STRICT_PASS"]
    reject_trades = [r for r in reader if r["overall_strict_price_verdict"] == "SLIPPAGE_OVERSHOOT_REJECT"]

    assert len(pass_trades) == 26, f"Expected 26 strict pass, got {len(pass_trades)}"
    assert len(reject_trades) == 2, f"Expected 2 rejects, got {len(reject_trades)}"

    reject_ids = {r["trade_id"] for r in reject_trades}
    assert reject_ids == {"V32_TR_03", "V32_TR_06"}


def test_cascade_tp_1m_sequence_audit():
    """
    Verifies that 1-minute Bybit resolution partitions the 15 partial trades into:
    - Exactly 8 MARKET_SEQUENCE_VERIFIED trades.
    - Exactly 7 UNRESOLVED_SEQUENCE trades.
    """
    assert CASCADE_CSV.exists(), f"Missing {CASCADE_CSV}"
    with open(CASCADE_CSV, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    assert len(reader) == 15, f"Expected 15 partial trades, got {len(reader)}"

    verified = [r for r in reader if r["cascade_sequence_status"] == "MARKET_SEQUENCE_VERIFIED"]
    unresolved = [r for r in reader if r["cascade_sequence_status"] == "UNRESOLVED_SEQUENCE"]

    assert len(verified) == 8, f"Expected 8 verified sequence, got {len(verified)}"
    assert len(unresolved) == 7, f"Expected 7 unresolved sequence, got {len(unresolved)}"

    verified_ids = {r["trade_id"] for r in verified}
    expected_verified = {
        "V32_TR_04", "V32_TR_07", "V32_TR_09", "V32_TR_12",
        "V32_TR_17", "V32_TR_19", "V32_TR_22", "V32_TR_25"
    }
    assert verified_ids == expected_verified


def test_economic_recalculation_all_scenarios():
    """
    Verifies that all recalculated economic scenarios match exact required numbers:
    - Baseline: +$17.99
    - VIP0 Fees: +$17.61
    - Realistic Slippage 0.05%: +$16.46
    - Robust without AKE: +$10.02
    - Severe Stress without AKE: +$7.82
    - Primary Verified Only: -$9.67
    """
    assert ECON_CSV.exists(), f"Missing {ECON_CSV}"
    with open(ECON_CSV, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    scenarios = {r["scenario_id"]: Decimal(r["net_pnl_usd"]) for r in reader}

    assert scenarios["SCEN_01_REPORTED_BASELINE"] == Decimal("17.99")
    assert scenarios["SCEN_02_STANDARD_VIP0_FEES"] == Decimal("17.61")
    assert scenarios["SCEN_03_REALISTIC_SLIPPAGE_005"] == Decimal("16.46")
    assert scenarios["SCEN_04_STRESS_SLIPPAGE_010"] == Decimal("14.26")
    assert scenarios["SCEN_05_NO_OUTLIER_SLIP_005"] == Decimal("10.02")
    assert scenarios["SCEN_06_NO_OUTLIER_STRESS_010"] == Decimal("7.82")
    assert scenarios["SCEN_07_PRIMARY_VERIFIED_ONLY"] == Decimal("-9.67")
    assert scenarios["SCEN_08_SEQUENCE_VERIFIED_SUBSET"] == Decimal("7.42")


def test_pit_lifecycle_shift_1_and_hourly_alignment():
    """
    Verifies Point-in-Time invariants:
    - All 28 trades have shift(1) verified.
    - Donchian lookback is 120 bars.
    - Signal candle close <= entry fill time.
    """
    assert LIFECYCLE_CSV.exists(), f"Missing {LIFECYCLE_CSV}"
    with open(LIFECYCLE_CSV, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    assert len(reader) == 28
    for r in reader:
        assert r["shift_1_verified"] == "TRUE"
        assert r["donchian_lookback_bars"] == "120"
        assert r["lifecycle_pit_status"] == "ZERO_LOOKAHEAD_VERIFIED"
        assert r["signal_candle_close_utc"] <= r["entry_fill_time_utc"]


def test_final_trade_verdict_structure():
    """Verifies that all 28 trades have an assigned evidence tier in 06_FINAL_TRADE_VERDICT.csv."""
    assert VERDICT_CSV.exists(), f"Missing {VERDICT_CSV}"
    with open(VERDICT_CSV, "r", encoding="utf-8") as f:
        reader = list(csv.DictReader(f))

    assert len(reader) == 28
    tiers = {r["evidence_tier"] for r in reader}
    assert "PRIMARY_VERIFIED" in tiers
    assert "MARKET_CONSISTENT_AND_SEQUENCE_VERIFIED" in tiers
    assert "MODEL_DERIVED_UNRESOLVED_SEQUENCE" in tiers
    assert "INVALID_SLIPPAGE_OVERSHOOT" in tiers
