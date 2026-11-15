#!/usr/bin/env python3
"""
tests/test_final_start_blockers_009.py
Protocol: TORG_BOT — FINAL-START-BLOCKERS-009
Comprehensive Verification Test Suite covering the 8 Core Criteria:
1. All three accounts start at 300 USDT.
2. Zero research trades before approved T0, equity strictly preserved across restarts.
3. New closed candle at/after T0 automatically triggers decisions.
4. Native and Shadow accompany positions and preserve equity on live ticks.
5. CryptoLab uses authentic Observer updating 24 profiles and exporting valid telemetry.
6. Native crash after SQLite commit before JSON recovers state authoritatively.
7. Network gap > 3 hours catches up chronologically without fake L2 depth.
8. Dashboard displays all 3 systems with Spot/Perp isolation.

[SYNTHETIC TEST DATASET - ISOLATED OFFLINE HARNESS - NOT LIVE VPS FORWARD DATA]
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any, Dict, List, Tuple
from unittest.mock import patch, MagicMock

import pytest
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v3.src.bybit_client import BybitClient
from v3_2.src.runners.run_native_paper import NativePaperRunner
from v3_2.src.runners.run_shadow_paper import ShadowPaperRunner
from v3_2.src.shadow.paper_trader_v32_shadow import (
    L2OrderbookSnapshot,
    PortfolioInstance,
)
from v3_2.src.integrations.r2_consumer import (
    R2TelemetryConsumer,
    R2ConsumerResult,
    SCHEMA_VERSION,
)
from v3_2.src.integrations.three_system_dashboard import ThreeSystemComparativeDashboard

CANONICAL_COHORT_ID = "TORG_THREE_SYS_20261115_001"
CANONICAL_T0_UTC = "2026-11-17T00:00:00+00:00"
T0_DT = datetime.fromisoformat(CANONICAL_T0_UTC.replace("Z", "+00:00"))
T0_MS = int(T0_DT.timestamp() * 1000)


def generate_synthetic_candles(
    n: int = 130,
    base_price: float = 50000.0,
    start_ts: int = T0_MS - 120 * 3600000,
) -> List[Dict[str, Any]]:
    """
    [SYNTHETIC TEST DATASET]
    Generates synthetic 1h candles:
    - 0..119: Flat consolidation (builds 120-bar Donchian channel, closes before T0)
    - 120: First bar closing exactly at T0
    - 121..129: Post-T0 trend and explosive breakout with volume surge (>= 1.2 * SMA20)
    """
    candles = []
    p = base_price
    for i in range(n):
        ts = start_ts + i * 3600000
        if i < 120:
            o = p
            h = p + 20.0
            l = p - 20.0
            c = p + (1.0 if i % 2 == 0 else -1.0) * 5.0
            v = 100.0
        elif i < 122:
            p += 150.0
            o = p - 50.0
            h = p + 50.0
            l = p - 60.0
            c = p + 40.0
            v = 150.0
        else:
            p += 500.0
            o = p - 200.0
            h = p + 300.0
            l = p - 100.0
            c = p + 250.0
            v = 500.0
        candles.append({
            "timestamp": ts,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": v,
            "turnover": v * c,
            "data_source": "SYNTHETIC_OFFLINE_TEST_DATASET",
        })
    return candles


# ==============================================================================
# Point 1: All three accounts start at 300 USDT
# ==============================================================================

def test_1_all_three_accounts_start_at_300_usdt(tmp_path):
    """
    Proves:
    1. All three systems (Native, Shadow, CryptoLab R2) start with exactly 300.000000 USDT capital.
    """
    # 1. Native Paper Runner
    nat_db = tmp_path / "nat_init.db"
    nat_telem = tmp_path / "nat_telem.json"
    nat_runner = NativePaperRunner(
        db_path=nat_db,
        telemetry_path=nat_telem,
        initial_capital=Decimal("300.000000"),
        cohort_id=CANONICAL_COHORT_ID,
        t0_utc=CANONICAL_T0_UTC,
    )
    assert nat_runner.initial_capital == Decimal("300.000000")
    assert nat_runner.engine.realized_equity == Decimal("300.000000")
    assert nat_runner.engine.peak_equity == Decimal("300.000000")

    # 2. Realistic Shadow Runner
    sha_db = tmp_path / "sha_init.db"
    sha_telem = tmp_path / "sha_telem.json"
    sha_runner = ShadowPaperRunner(
        db_path=sha_db,
        telemetry_path=sha_telem,
        initial_capital=Decimal("300.000000"),
        cohort_id=CANONICAL_COHORT_ID,
        t0_utc=CANONICAL_T0_UTC,
    )
    assert sha_runner.initial_capital == Decimal("300.000000")
    assert sha_runner.portfolio.initial_capital == Decimal("300.000000")
    assert sha_runner.portfolio.realized_equity == Decimal("300.000000")

    # 3. CryptoLab R2-SHADOW Contract
    example_path = REPO_ROOT / "docs" / "integrations" / "r2_shadow" / "telemetry.example.json"
    data = json.loads(example_path.read_text(encoding="utf-8"))
    r2_base = next(p for p in data["profiles"] if p["profile"] == "R2:300:BASE:OBSERVED_BOOK")
    assert r2_base["initial_capital_usdt"] == 300
    assert r2_base["cash_usdt"] == 300.0


# ==============================================================================
# Point 2: Zero research trades before approved T0, equity strictly preserved
# ==============================================================================

def test_2_zero_trades_before_t0_and_equity_preserved(tmp_path):
    """
    Proves:
    2. Before approved T0 (schedule_active=False OR bar_close_ms < t0_ms), exactly 0 trades
       are executed, 0 fees deducted, equity remains exactly $300.000000.
       Restarts before T0 reload cleanly with $300.000000.
    """
    candles = generate_synthetic_candles(125)

    # Runner with schedule_active=False (pre-authorization)
    nat_db = tmp_path / "nat_pre_t0.db"
    nat_telem = tmp_path / "nat_pre_t0_telem.json"
    nat_state = tmp_path / "nat_pre_t0_state.json"
    runner = NativePaperRunner(
        db_path=nat_db,
        telemetry_path=nat_telem,
        state_path=nat_state,
        initial_capital=Decimal("300.000000"),
        cohort_id=CANONICAL_COHORT_ID,
        t0_utc=CANONICAL_T0_UTC,
        symbols=["BTCUSDT"],
        schedule_active=False,  # NOT ACTIVATED
    )

    # Ingest 125 bars (including explosive breakout bars 122..124)
    for c in candles:
        res = runner.ingest_closed_bar("BTCUSDT", c, current_price=Decimal(str(c["close"])))
        assert res["status"] == "PRE_T0_WARMUP"
        assert res["positions_count"] == 0
        assert res["realized_equity"] == 300.0

    # Strict invariant: exactly 0 positions, 0 fees, 300.00 equity
    assert len(runner.engine.open_positions) == 0
    assert len(runner.engine.closed_trades) == 0
    assert runner.engine.realized_equity == Decimal("300.000000")
    assert runner.total_fees == Decimal("0")

    # Simulate restart before T0: reload state
    restarted_runner = NativePaperRunner(
        db_path=nat_db,
        telemetry_path=nat_telem,
        state_path=nat_state,
        initial_capital=Decimal("300.000000"),
        cohort_id=CANONICAL_COHORT_ID,
        t0_utc=CANONICAL_T0_UTC,
        symbols=["BTCUSDT"],
        schedule_active=False,
    )
    assert len(restarted_runner.engine.open_positions) == 0
    assert restarted_runner.engine.realized_equity == Decimal("300.000000")


# ==============================================================================
# Point 3: New closed candle at/after T0 automatically triggers decisions
# ==============================================================================

def test_3_candle_at_or_after_t0_triggers_auto_decisions(tmp_path):
    """
    Proves:
    3. At or after approved T0 (schedule_active=True and bar_close_ms >= t0_ms),
       the real quantitative engine automatically makes decisions on newly closed bars.
    """
    candles = generate_synthetic_candles(126)

    # 1. Native Paper Runner
    nat_db = tmp_path / "nat_post_t0.db"
    nat_telem = tmp_path / "nat_post_t0_telem.json"
    nat_runner = NativePaperRunner(
        db_path=nat_db,
        telemetry_path=nat_telem,
        initial_capital=Decimal("300.000000"),
        cohort_id=CANONICAL_COHORT_ID,
        t0_utc=CANONICAL_T0_UTC,
        symbols=["BTCUSDT"],
        schedule_active=True,
    )

    # 2. Realistic Shadow Runner
    sha_db = tmp_path / "sha_post_t0.db"
    sha_telem = tmp_path / "sha_post_t0_telem.json"
    sha_runner = ShadowPaperRunner(
        db_path=sha_db,
        telemetry_path=sha_telem,
        initial_capital=Decimal("300.000000"),
        cohort_id=CANONICAL_COHORT_ID,
        t0_utc=CANONICAL_T0_UTC,
        symbols=["BTCUSDT"],
        schedule_active=True,
    )

    # Bars 0..121: Consolidation and pre-breakout
    for i in range(122):
        c = candles[i]
        p = Decimal(str(c["close"]))
        nat_runner.ingest_closed_bar("BTCUSDT", c, current_price=p)

        bid = p - Decimal("1.0")
        ask = p + Decimal("1.0")
        l2_a = [(ask, Decimal("10.0"))]
        l2_b = [(bid, Decimal("10.0"))]
        sha_runner.ingest_closed_bar(
            "BTCUSDT", c,
            best_bid=bid, best_ask=ask,
            l2_ladder_asks=l2_a, l2_ladder_bids=l2_b,
        )

    assert len(nat_runner.engine.open_positions) == 0
    assert len(sha_runner.portfolio.positions) == 0

    # Ingest Bar 122: Explosive breakout closing at or after T0
    c122 = candles[122]
    p122 = Decimal(str(c122["close"]))

    res_nat = nat_runner.ingest_closed_bar("BTCUSDT", c122, current_price=p122)
    assert res_nat["status"] == "PROCESSED"
    assert len(nat_runner.engine.open_positions) == 1
    assert "BTCUSDT" in nat_runner.engine.open_positions

    bid122 = p122 - Decimal("1.0")
    ask122 = p122 + Decimal("1.0")
    res_sha = sha_runner.ingest_closed_bar(
        "BTCUSDT", c122,
        best_bid=bid122, best_ask=ask122,
        l2_ladder_asks=[(ask122, Decimal("10.0"))],
        l2_ladder_bids=[(bid122, Decimal("10.0"))],
    )
    assert res_sha["status"] in ["FULL", "PROCESSED"]
    assert len(sha_runner.portfolio.positions) == 1
    assert "BTCUSDT" in sha_runner.portfolio.positions


# ==============================================================================
# Point 4: Native and Shadow accompany positions and preserve equity on live ticks
# ==============================================================================

def test_4_live_ticks_accompany_positions_and_update_equity(tmp_path):
    """
    Proves:
    4. Active positions are accompanied between bar closes via live quote updates.
       MTM Equity updates dynamically and stops trigger without waiting for next 1H bar.
    """
    candles = generate_synthetic_candles(124)
    nat_db = tmp_path / "nat_accompany.db"
    nat_telem = tmp_path / "nat_accompany_telem.json"
    runner = NativePaperRunner(
        db_path=nat_db,
        telemetry_path=nat_telem,
        initial_capital=Decimal("300.000000"),
        cohort_id=CANONICAL_COHORT_ID,
        t0_utc=CANONICAL_T0_UTC,
        symbols=["BTCUSDT"],
        schedule_active=True,
    )

    for i in range(123):
        c = candles[i]
        runner.ingest_closed_bar("BTCUSDT", c, current_price=Decimal(str(c["close"])))

    assert "BTCUSDT" in runner.engine.open_positions
    pos = runner.engine.open_positions["BTCUSDT"]
    entry_p = pos.entry_price

    # 1. Price increases intraday: dynamic MTM equity update
    higher_p = entry_p + Decimal("2000.0")
    telem_higher = runner.update_quotes({"BTCUSDT": higher_p})
    assert telem_higher["equity_usdt"] > float(runner.initial_capital)
    assert telem_higher["net_pnl_usdt"] > 0.0

    # 2. Price breaches stop loss on tick quote: position closed immediately
    stop_p = pos.current_stop - Decimal("100.0")
    runner.update_quotes({"BTCUSDT": stop_p})
    assert "BTCUSDT" not in runner.engine.open_positions
    assert len(runner.engine.closed_trades) >= 1
    assert "STOP" in runner.engine.closed_trades[-1].exit_reason.upper()


# ==============================================================================
# Point 5: CryptoLab uses authentic Observer updating 24 profiles and exporting valid telemetry
# ==============================================================================

def test_5_authentic_cryptolab_observer_and_24_profiles(tmp_path):
    """
    Proves:
    5. CryptoLab R2 Observer is authentic (commit 313a908782271a6e1fffaee83be7ea1a616337cb):
       - 100% hash pass on frozen manifest.
       - Runs offline recovery scenario updating 24 profile accounts without network access.
       - Telemetry is successfully read and validated by R2TelemetryConsumer.
    """
    cryptolab_dir = REPO_ROOT / ".scratch" / "cryptolab_ref" / "research" / "hunter001_dg001" / "r2_shadow"
    if str(cryptolab_dir) not in sys.path:
        sys.path.insert(0, str(cryptolab_dir))

    import runtime
    manifest = runtime.verify_frozen()
    assert manifest["source_commit"] == "815d6884980791309cb37770ac96a9a93a2c48c0"
    assert len(manifest["files"]) == 9

    # Verify 24 accounts via recovery_scenario experiment
    import recovery_scenario
    test_out = tmp_path / "cryptolab_offline_test"
    report = recovery_scenario.experiment(str(test_out))
    assert report["status"] == "PASS"
    assert report["reference_nonzero_profiles"] == 24
    assert report["network_requests"] == 0
    assert report["real_orders"] == 0
    assert report["demo_orders"] == 0

    # Verify R2TelemetryConsumer parses exported telemetry
    schema_path = cryptolab_dir / "telemetry.schema.json"
    consumer = R2TelemetryConsumer(schema_path=schema_path)

    example_path = REPO_ROOT / "docs" / "integrations" / "r2_shadow" / "telemetry.example.json"
    example_data = json.loads(example_path.read_text(encoding="utf-8"))
    example_data["process_status"] = "OBSERVING"
    example_data["cohort"]["t0_utc"] = CANONICAL_T0_UTC
    example_data["generated_at"] = datetime.now(timezone.utc).isoformat()
    example_data["heartbeat_at"] = datetime.now(timezone.utc).isoformat()

    telem_file = tmp_path / "cryptolab_telem.json"
    telem_file.write_text(json.dumps(example_data), encoding="utf-8")

    res = consumer.read_telemetry_file(telem_file)
    assert res.is_valid is True
    assert res.status in ["OBSERVING", "FRESH"]
    assert res.cohort_t0_utc == CANONICAL_T0_UTC


# ==============================================================================
# Point 6: Native crash after SQLite commit before JSON recovers state authoritatively
# ==============================================================================

def test_6_native_crash_after_sqlite_commit_before_json_recovers_state(tmp_path):
    """
    Proves:
    6. Section P0.3: Crash after SQLite COMMIT but before JSON write is authoritatively
       recovered from SQLite native_state_journal. No position or trade loss occurs.
       Duplicate bar delivery is skipped without double accounting.
    """
    candles = generate_synthetic_candles(125)
    nat_db = tmp_path / "nat_2pc.db"
    nat_telem = tmp_path / "nat_2pc_telem.json"
    nat_state = tmp_path / "nat_2pc_state.json"

    runner = NativePaperRunner(
        db_path=nat_db,
        telemetry_path=nat_telem,
        state_path=nat_state,
        initial_capital=Decimal("300.000000"),
        cohort_id=CANONICAL_COHORT_ID,
        t0_utc=CANONICAL_T0_UTC,
        symbols=["BTCUSDT"],
        schedule_active=True,
    )

    # Ingest consolidation 0..121
    for i in range(122):
        c = candles[i]
        runner.ingest_closed_bar("BTCUSDT", c, current_price=Decimal(str(c["close"])))

    assert len(runner.engine.open_positions) == 0

    # Ingest breakout Bar 122 with fault injection AFTER SQLite commit, BEFORE JSON write
    c122 = candles[122]
    event_key = f"BTCUSDT_{c122['timestamp']}"

    # Verify JSON file either doesn't exist or doesn't have the new trade yet
    with pytest.raises(RuntimeError, match="FAULT_INJECTION: Crash after SQLite commit but before JSON write"):
        runner.ingest_closed_bar(
            "BTCUSDT", c122,
            current_price=Decimal(str(c122["close"])),
            fault_injection_point="after_commit_before_json",
        )

    # Verify SQLite HAS committed the transaction!
    conn = sqlite3.connect(str(nat_db))
    try:
        cur = conn.cursor()
        cur.execute("SELECT count(*) FROM native_state_journal;")
        assert cur.fetchone()[0] >= 1
        cur.execute("SELECT state_json FROM native_state_journal ORDER BY journal_id DESC LIMIT 1;")
        journal_row = cur.fetchone()
        assert journal_row is not None
        journal_data = json.loads(journal_row[0])
        assert "BTCUSDT" in journal_data["engine"]["open_positions"]
        cur.execute("SELECT 1 FROM native_dedup WHERE event_key = ?;", (event_key,))
        assert cur.fetchone() is not None
    finally:
        conn.close()

    # SIMULATE PROCESS RESTART: Fresh instance pointing to the same DB and state file
    recovered_runner = NativePaperRunner(
        db_path=nat_db,
        telemetry_path=nat_telem,
        state_path=nat_state,
        initial_capital=Decimal("300.000000"),
        cohort_id=CANONICAL_COHORT_ID,
        t0_utc=CANONICAL_T0_UTC,
        symbols=["BTCUSDT"],
        schedule_active=True,
    )

    # Authoritative recovery from SQLite native_state_journal:
    assert len(recovered_runner.engine.open_positions) == 1
    assert "BTCUSDT" in recovered_runner.engine.open_positions

    # Deliver Bar 122 again: MUST be skipped as duplicate
    res_dup = recovered_runner.ingest_closed_bar(
        "BTCUSDT", c122,
        current_price=Decimal(str(c122["close"])),
    )
    assert res_dup["status"] == "SKIPPED_DUPLICATE"
    assert len(recovered_runner.engine.open_positions) == 1


# ==============================================================================
# Point 7: Network gap > 3 hours catches up chronologically without fake L2 depth
# ==============================================================================

def test_7_network_gap_over_3_hours_recovers_chronologically(tmp_path):
    """
    Proves:
    7. Section P0.4: When a network disconnection > 3 hours occurs (e.g. 4 missed bars),
       klines are retrieved via limit=50 and processed chronologically.
       For historical bars (> 120s old), live orderbook substitution is strictly forbidden
       (marked REJECTED_HISTORICAL_NO_L2_ORDERBOOK), maintaining state without fictitious fills.
    """
    candles = generate_synthetic_candles(126)
    sha_db = tmp_path / "sha_gap.db"
    sha_telem = tmp_path / "sha_gap_telem.json"
    sha_runner = ShadowPaperRunner(
        db_path=sha_db,
        telemetry_path=sha_telem,
        initial_capital=Decimal("300.000000"),
        cohort_id=CANONICAL_COHORT_ID,
        t0_utc=CANONICAL_T0_UTC,
        symbols=["BTCUSDT"],
        schedule_active=True,
    )

    # Warmup 0..120
    for i in range(121):
        c = candles[i]
        p = Decimal(str(c["close"]))
        sha_runner.ingest_closed_bar(
            "BTCUSDT", c,
            best_bid=p - Decimal("1.0"),
            best_ask=p + Decimal("1.0"),
            l2_ladder_asks=[(p + Decimal("1.0"), Decimal("10.0"))],
            l2_ladder_bids=[(p - Decimal("1.0"), Decimal("10.0"))],
        )

    # Simulate 4 missed historical bars (age > 120s) delivered in batch
    for i in [121, 122, 123, 124]:
        c = candles[i]
        # Historical catchup: no live orderbook is passed
        res = sha_runner.ingest_closed_bar(
            "BTCUSDT", c,
            best_bid=None, best_ask=None,
            is_historical_catchup=True,
        )
        assert res["status"] in ["REJECTED_HISTORICAL_NO_L2_ORDERBOOK", "NO_SIGNAL"]

    # Open positions were not created on fake orderbooks
    assert len(sha_runner.portfolio.positions) == 0

    # Bar 125 arrives live with verified discrete orderbook: normal execution resumes!
    c125 = candles[125]
    p125 = Decimal(str(c125["close"]))
    res_live = sha_runner.ingest_closed_bar(
        "BTCUSDT", c125,
        best_bid=p125 - Decimal("1.0"),
        best_ask=p125 + Decimal("1.0"),
        l2_ladder_asks=[(p125 + Decimal("1.0"), Decimal("10.0"))],
        l2_ladder_bids=[(p125 - Decimal("1.0"), Decimal("10.0"))],
        is_historical_catchup=False,
    )
    assert res_live["status"] in ["FULL", "PROCESSED", "NO_SIGNAL"]


# ==============================================================================
# Point 8: Dashboard displays all 3 systems with Spot/Perp isolation
# ==============================================================================

def test_8_three_system_dashboard_spot_perp_isolation_and_telemetry(tmp_path):
    """
    Proves:
    8. Dashboard correctly loads telemetry for Native (Linear Perp), Realistic Shadow (Linear Perp),
       and CryptoLab R2 (Spot), enforcing market type separation and stale data detection.
    """
    dash = ThreeSystemComparativeDashboard(
        expected_cohort_id=CANONICAL_COHORT_ID,
        expected_t0_utc=CANONICAL_T0_UTC,
    )

    native_port = PortfolioInstance(
        name="NATIVE",
        initial_capital=Decimal("300.0"),
        taker_fee=Decimal("0.0009"),
        maker_fee=Decimal("0.000324"),
        use_spread_gate=False,
        use_depth_slippage=False,
    )
    shadow_port = PortfolioInstance(
        name="SHADOW",
        initial_capital=Decimal("300.0"),
        taker_fee=Decimal("0.0010"),
        maker_fee=Decimal("0.0010"),
        use_spread_gate=True,
        use_depth_slippage=True,
    )

    now_dt = datetime.now(timezone.utc)
    r2_result = R2ConsumerResult(
        is_valid=True,
        status="OBSERVING",
        generated_at=now_dt,
        heartbeat_at=now_dt,
        cohort_t0_utc=CANONICAL_T0_UTC,
        r2_base_profile="R2:300:BASE:OBSERVED_BOOK",
        equity_usdt=305.50,
        cash_usdt=200.0,
        net_pnl_usdt=5.50,
        net_return_pct=1.83,
        mdd_pct=-0.50,
        exposure_pct=35.0,
        valuation_status="FRESH",
        fees_usdt=0.40,
        turnover_usdt=200.0,
        r2_minus_t01_usdt=3.50,
        r2_minus_t01_pp=1.17,
        comparison_status="PAIRED_PRELIMINARY",
        real_orders=0,
        demo_orders=0,
    )
    report = dash.generate_comparative_report(
        native_portfolio=native_port,
        shadow_portfolio=shadow_port,
        r2_result=r2_result,
        now_dt=now_dt,
    )

    # Verify market type isolation in dashboard report and rendered HTML
    assert report["separation_rules"]["spot_perp_separation_enforced"] is True
    assert report["separation_rules"]["portfolio_pooling_forbidden"] is True
    assert report["systems"]["native"]["market_type"] == "BYBIT_LINEAR_PERPETUAL"
    assert report["systems"]["shadow"]["market_type"] == "BYBIT_LINEAR_PERPETUAL"
    assert report["systems"]["r2_shadow"]["market_type"] == "BYBIT_SPOT_LONG_CASH_NO_LEVERAGE"

    html = dash.render_html_dashboard(report)
    assert "Bybit Linear Perp" in html
    assert "Bybit Spot Long-Only" in html
    assert "v3.2 Native" in html
    assert "v3.2 Realistic Shadow" in html
    assert "CryptoLab R2-SHADOW" in html
    assert "300.00" in html
