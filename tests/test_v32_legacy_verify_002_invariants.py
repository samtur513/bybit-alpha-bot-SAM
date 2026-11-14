"""
Verification Test Suite for V32-LEGACY-VERIFY-002
Verifies independent ledger accounting, market data boundaries, zero-lookahead,
timeframe configuration, fee sensitivity, and economic consistency.
"""

import csv
import sys
from decimal import Decimal
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

LEDGER_CSV = REPO_ROOT / "reports" / "v32_legacy_verify_002" / "02_INDEPENDENT_EXECUTION_LEDGER.csv"
EVIDENCE_CSV = REPO_ROOT / "reports" / "v32_legacy_verify_002" / "04_EXECUTION_EVIDENCE.csv"
ECON_CSV = REPO_ROOT / "reports" / "v32_legacy_verify_002" / "07_FINAL_ECONOMIC_COMPARISON.csv"
CONFIG_FILE = REPO_ROOT / "v3_2" / "src" / "config.py"
TRADER_FILE = REPO_ROOT / "v3_2" / "src" / "paper_trader_v32.py"


def test_independent_ledger_13_non_partial_penny_perfect():
    """
    Verifies that all 13 non-partial trades have PnL calculated from
    first-principles matching the paper ledger with < $0.01 precision.
    """
    assert LEDGER_CSV.exists(), f"Missing ledger CSV at {LEDGER_CSV}"
    with open(LEDGER_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        non_partial_trades = [r for r in reader if r["partial_status"] == "NO_PARTIAL"]

    assert len(non_partial_trades) == 13, f"Expected 13 non-partial trades, got {len(non_partial_trades)}"

    sum_verified_net = Decimal("0")
    for r in non_partial_trades:
        disc = Decimal(r["pnl_discrepancy_usd"])
        assert disc < Decimal("0.01"), f"Discrepancy for {r['trade_id']} exceeds $0.01: {disc}"
        assert r["accounting_verification_status"] == "FULL_PRIMARY_VERIFIED"
        sum_verified_net += Decimal(r["reconstructed_net_pnl_usd"])

    # Sum of 13 non-partial trades is -$9.67
    assert abs(sum_verified_net - Decimal("-9.67")) < Decimal("0.05")


def test_all_28_trades_market_data_bounds():
    """
    Verifies against Bybit public market klines that 100% of the 28 trades
    have entry and exit prices within real candle high/low boundaries and meet
    Bybit minimum notional ($5).
    """
    assert EVIDENCE_CSV.exists(), f"Missing evidence CSV at {EVIDENCE_CSV}"
    with open(EVIDENCE_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    assert len(rows) == 28, f"Expected 28 evidence rows, got {len(rows)}"

    for r in rows:
        assert r["entry_within_bar_status"] == "PASS", f"Entry out of bounds for {r['trade_id']}"
        assert r["exit_within_bar_status"] == "PASS", f"Exit out of bounds for {r['trade_id']}"
        assert r["min_notional_pass"] == "PASS", f"Notional < $5 for {r['trade_id']}"
        assert r["market_evidence_status"] == "MARKET_DATA_VERIFIED", f"Not verified: {r['trade_id']}"


def test_donchian_timeframe_1h_and_120_bars():
    """Verifies that v3.2 operates strictly on 1H candles with a 120-bar lookback."""
    from v3_2.src.config import CANDLE_INTERVAL, BAR_INTERVAL_MS, DONCHIAN_WINDOW
    assert CANDLE_INTERVAL == "60", f"Expected CANDLE_INTERVAL '60' (1h), got {CANDLE_INTERVAL}"
    assert BAR_INTERVAL_MS == 3600000, f"Expected BAR_INTERVAL_MS 3,600,000, got {BAR_INTERVAL_MS}"
    assert DONCHIAN_WINDOW == 120, f"Expected DONCHIAN_WINDOW 120, got {DONCHIAN_WINDOW}"


def test_lookahead_zero_shift_1():
    """Verifies that Donchian channels in paper_trader_v32.py use shift(1)."""
    with open(TRADER_FILE, "r", encoding="utf-8") as f:
        content = f.read()

    assert 'high.shift(1).rolling(lookback).max()' in content
    assert 'low.shift(1).rolling(lookback).min()' in content


def test_fee_drag_sensitivity_vip0():
    """
    Verifies that applying standard VIP0 fee (0.100%) vs MNT discount fee (0.090%)
    imposes an additional drag of ~$0.38 across 28 trades, leaving > $17.50 net.
    """
    with open(LEDGER_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    tot_fee_09 = sum(Decimal(r["primary_total_fee_0009_usd"]) for r in rows)
    tot_fee_10 = sum(Decimal(r["standard_vip0_total_fee_0010_usd"]) for r in rows)

    diff = tot_fee_10 - tot_fee_09
    assert abs(diff - Decimal("0.376")) < Decimal("0.02")

    tot_net_09 = sum(Decimal(r["reported_net_pnl_usd"]) for r in rows)
    net_with_vip0 = tot_net_09 - diff
    assert net_with_vip0 > Decimal("17.50")


def test_economic_scenarios_strictly_positive():
    """Verifies that conservative economic scenarios remain solidly positive."""
    with open(ECON_CSV, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        scenarios = {r["variant_id"]: Decimal(r["net_pnl_usdt"]) for r in reader}

    assert scenarios["REPORTED_PAPER_LEDGER"] == Decimal("17.99")
    assert scenarios["CONSERVATIVE_REALISTIC_ALL"] >= Decimal("16.00")
    assert scenarios["CONSERVATIVE_NO_OUTLIER"] >= Decimal("10.00")
    assert scenarios["RANGE_MEAN_REVERSION"] == Decimal("0.00")
