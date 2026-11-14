#!/usr/bin/env python3
"""
tests/test_v32_parity_and_execution_proof.py
Comprehensive test suite verifying V32-SHADOW-PARITY-003 requirements:
1. Signal Parity (P0.1): Direct invocation of original CoreV3Engine / MultiCoinScanner / RegimeGuard.
2. Independent Risk Managers & АКПП (P0.2): Complete decoupling of KellyRiskManager, peak equity, and gear shifts.
3. Intrabar Sequencing (P0.3): AMBIGUOUS_SEQUENCE detection and conservative Stop Loss execution.
4. Discrete L2 Depth (P0.5): Discrete orderbook ladder walking (VWAP) and INSUFFICIENT_L2_DEPTH rejection.
5. Perpetual Funding Rate (P0.5): Strict 8-hour funding settlement based on actual position hold time.
6. Atomic SQLite State Journal & 5-Point Crash Recovery (P0.4): Transactional recovery from journal.
"""

from decimal import Decimal, ROUND_FLOOR
import os
from pathlib import Path
import sqlite3
import tempfile
import pytest

from v3.src.core_v3 import CoreV3Engine
from v3.src.multi_scanner import MultiCoinScanner
from v3_2.src.shadow.paper_trader_v32_shadow import (
    V32LegacyShadowEngine,
    PortfolioInstance,
    PositionState,
    TradeRecord,
    walk_discrete_l2,
    INITIAL_CAPITAL,
    NATIVE_TAKER_FEE,
    SHADOW_TAKER_FEE,
    MAX_ALLOWED_SPREAD_PCT,
)


@pytest.fixture
def shadow_env():
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    tmpdir = tmp.name
    db_path = os.path.join(tmpdir, "test_parity_events.db")
    state_file = os.path.join(tmpdir, "test_parity_state.json")
    engine = V32LegacyShadowEngine(
        initial_capital=Decimal("300.000000"),
        db_path=db_path,
        state_file=state_file,
        enable_news_defense=True,
        enable_auto_gearbox=True,
    )
    yield engine, db_path, state_file
    try:
        tmp.cleanup()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# P0.1: Signal Parity and Original Core Components
# ---------------------------------------------------------------------------
def test_signal_identity_with_core_v3_scanner(shadow_env):
    """
    P0.1: Verifies that V32LegacyShadowEngine uses MultiCoinScanner from CoreV3
    and produces 100% identical breakout signals on identical market inputs.
    """
    engine, db_path, state_file = shadow_env

    # Standalone scanner from CoreV3
    standalone_scanner = MultiCoinScanner(min_adx=18.0, min_volume_ratio=0.0)

    test_bar = {
        "high": "105.0",
        "low": "99.0",
        "close": "104.5",
        "upper": "103.0",
        "lower": "95.0",
        "atr": "2.0",
        "adx": 25.0,
        "ema_fast": 102.0,
        "ema_slow": 98.0,
        "volume": 50000.0,
        "volume_sma": 40000.0,
    }

    # Evaluate via standalone CoreV3 scanner
    standalone_cand = standalone_scanner.evaluate_symbol_bar(
        symbol="SOLUSDT",
        close=float(test_bar["close"]),
        upper_channel=float(test_bar["upper"]),
        lower_channel=float(test_bar["lower"]),
        ema_fast=float(test_bar["ema_fast"]),
        ema_slow=float(test_bar["ema_slow"]),
        atr=float(test_bar["atr"]),
        volume=test_bar["volume"],
        volume_sma=test_bar["volume_sma"],
        adx=test_bar["adx"],
    )

    # Evaluate via Shadow Engine's embedded Core scanner
    engine_cand = engine.scanner.evaluate_symbol_bar(
        symbol="SOLUSDT",
        close=float(test_bar["close"]),
        upper_channel=float(test_bar["upper"]),
        lower_channel=float(test_bar["lower"]),
        ema_fast=float(test_bar["ema_fast"]),
        ema_slow=float(test_bar["ema_slow"]),
        atr=float(test_bar["atr"]),
        volume=test_bar["volume"],
        volume_sma=test_bar["volume_sma"],
        adx=test_bar["adx"],
    )

    assert standalone_cand is not None
    assert engine_cand is not None
    assert standalone_cand.symbol == engine_cand.symbol == "SOLUSDT"
    assert standalone_cand.direction == engine_cand.direction == 1
    assert standalone_cand.close_price == engine_cand.close_price == Decimal("104.5")
    assert standalone_cand.breakout_boundary == engine_cand.breakout_boundary == Decimal("103.0")
    assert standalone_cand.trend_momentum_score == engine_cand.trend_momentum_score


# ---------------------------------------------------------------------------
# P0.2: Decoupled Independent Risk Managers & АКПП (Gear Shifts)
# ---------------------------------------------------------------------------
def test_independent_risk_managers_and_gearbox_decoupling(shadow_env):
    """
    P0.2: Verifies that Native and Shadow maintain completely separate KellyRiskManager
    instances. When Shadow incurs losses and downshifts from Gear 3 to Gear 2,
    Native's risk manager remains in Gear 3 with 100% position sizing.
    """
    engine, db_path, state_file = shadow_env

    # Initial state: Both Native and Shadow in Gear 3
    assert engine.native_gear == 3
    assert engine.shadow_gear == 3
    assert engine.native_risk_manager.get_effective_base_risk_pct() == Decimal("0.010")
    assert engine.shadow_risk_manager.get_effective_base_risk_pct() == Decimal("0.010")

    # Force Shadow into drawdown (> 8.0% drops gear from 3 to 2)
    # Peak is 300.0, drops to 270.0 (10% drawdown)
    engine.shadow_portfolio.realized_equity = Decimal("270.000000")
    engine.shadow_portfolio.peak_equity = Decimal("300.000000")
    engine.shadow_risk_manager.update_drawdown(
        engine.shadow_portfolio.realized_equity,
        engine.shadow_portfolio.peak_equity
    )

    # Shadow MUST have downshifted to Gear 2 (50% risk = 0.50%)
    assert engine.shadow_gear == 2
    assert engine.shadow_risk_manager.get_effective_base_risk_pct() == Decimal("0.005")

    # Native MUST REMAIN completely unaffected in Gear 3 (100% risk = 1.0%)
    assert engine.native_gear == 3
    assert engine.native_risk_manager.get_effective_base_risk_pct() == Decimal("0.010")
    assert engine.native_portfolio.realized_equity == Decimal("300.000000")


# ---------------------------------------------------------------------------
# P0.3: Intrabar Sequencing & Ambiguity Guard (AMBIGUOUS_SEQUENCE)
# ---------------------------------------------------------------------------
def test_intrabar_ambiguous_sequence_conservative_stop(shadow_env):
    """
    P0.3: When both Stop Loss and Take Profit levels are reached within the same 1h
    candle aggregate without tick timestamps, the engine MUST:
    1. Log AMBIGUOUS_SEQUENCE in SQLite.
    2. Conservatively execute STOP LOSS.
    3. Strictly NOT set tp1_taken to True or ratchet stop to Breakeven.
    """
    engine, db_path, state_file = shadow_env

    entry_ts = 1700000000000
    res_entry = engine.process_closed_bar_event(
        exchange_ts=entry_ts,
        symbol="BTCUSDT",
        bar={
            "high": "50500.0",
            "low": "49500.0",
            "close": "50000.0",
            "upper": "49800.0",
            "lower": "45000.0",
            "atr": "400.0",
            "adx": 28.0,
            "ema_fast": 49900.0,
            "ema_slow": 47000.0,
        },
        best_bid=Decimal("49998.0"),
        best_ask=Decimal("50002.0"),
        depth_bid_usdt=Decimal("500000.0"),
        depth_ask_usdt=Decimal("500000.0"),
    )
    assert "BTCUSDT" in engine.shadow_portfolio.positions
    pos = engine.shadow_portfolio.positions["BTCUSDT"]
    entry_price = pos.entry_price
    initial_stop = pos.current_stop  # entry - 2.5 * 400 = entry - 1000
    tp1_target = entry_price + Decimal("1.5") * Decimal("400.0") # entry + 600

    # Bar 2: Huge spike and dump touching BOTH TP1 (high >= entry + 600) AND Stop Loss (low <= entry - 1000)
    bar2_ts = entry_ts + 3600000
    res_bar2 = engine.process_closed_bar_event(
        exchange_ts=bar2_ts,
        symbol="BTCUSDT",
        bar={
            "high": str(tp1_target + Decimal("100.0")),  # Touched TP1!
            "low": str(initial_stop - Decimal("50.0")),   # Touched Stop Loss!
            "close": str(entry_price),
            "upper": "51000.0",
            "lower": "45000.0",
            "atr": "400.0",
            "adx": 28.0,
            "ema_fast": 49900.0,
            "ema_slow": 47000.0,
        },
        best_bid=Decimal("49998.0"),
        best_ask=Decimal("50002.0"),
        depth_bid_usdt=Decimal("500000.0"),
        depth_ask_usdt=Decimal("500000.0"),
    )

    # Position MUST be closed via STOP_LOSS, not TP1
    assert "BTCUSDT" not in engine.shadow_portfolio.positions
    assert len(engine.shadow_portfolio.closed_trades) == 1
    closed_trade = engine.shadow_portfolio.closed_trades[0]
    assert closed_trade.exit_reason == "STOP_LOSS"
    assert closed_trade.net_pnl < Decimal("0")  # Incurred loss

    # Verify AMBIGUOUS_SEQUENCE was logged to SQLite
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT event_type, execution_status, notes FROM shadow_execution_events WHERE event_type='AMBIGUOUS_SEQUENCE';")
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == "AMBIGUOUS_SEQUENCE"
        assert row[1] == "AMBIGUOUS"
        assert "Intra-bar collision" in row[2]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# P0.5: Discrete L2 Orderbook Depth Walking & VWAP Slippage
# ---------------------------------------------------------------------------
def test_discrete_l2_orderbook_walking_and_rejection():
    """
    P0.5: Tests discrete orderbook ladder walking:
    1. Computes exact VWAP across discrete levels.
    2. Rejects when orderbook depth is insufficient for requested quantity.
    """
    # Level 1: 0.5 @ 100.0, Level 2: 1.0 @ 101.0, Level 3: 2.0 @ 102.0
    ladder = [
        (Decimal("100.0"), Decimal("0.5")),
        (Decimal("101.0"), Decimal("1.0")),
        (Decimal("102.0"), Decimal("2.0")),
    ]

    # Case A: Request 1.0 units (0.5 from L1 @ 100.0, 0.5 from L2 @ 101.0)
    # Total cost = 0.5*100 + 0.5*101 = 50.0 + 50.5 = 100.5
    # VWAP = 100.5 / 1.0 = 100.5
    succ, vwap, filled_qty = walk_discrete_l2(ladder, Decimal("1.0"))
    assert succ is True
    assert filled_qty == Decimal("1.0")
    assert vwap == Decimal("100.5")

    # Case B: Request 4.0 units (available is 0.5 + 1.0 + 2.0 = 3.5 < 4.0)
    succ_b, vwap_b, filled_qty_b = walk_discrete_l2(ladder, Decimal("4.0"))
    assert succ_b is False
    assert filled_qty_b == Decimal("3.5")


def test_shadow_execution_discrete_l2_rejection(shadow_env):
    """
    P0.5: Tests that Shadow portfolio rejects entry when discrete L2 orderbook
    depth is insufficient, while Native paper executes.
    """
    engine, db_path, state_file = shadow_env

    # Shallow ladder with only 0.001 BTC depth
    shallow_ladder_asks = [
        (Decimal("50000.0"), Decimal("0.001")),
    ]

    res = engine.process_closed_bar_event(
        exchange_ts=1700000000000,
        symbol="BTCUSDT",
        bar={
            "high": "50500.0",
            "low": "49500.0",
            "close": "50000.0",
            "upper": "49800.0",
            "lower": "45000.0",
            "atr": "400.0",
            "adx": 28.0,
            "ema_fast": 49900.0,
            "ema_slow": 47000.0,
        },
        best_bid=Decimal("49998.0"),
        best_ask=Decimal("50002.0"),
        depth_bid_usdt=Decimal("500000.0"),
        depth_ask_usdt=Decimal("500000.0"),
        l2_ladder_asks=shallow_ladder_asks,
    )

    # Native must have accepted
    assert "BTCUSDT" in engine.native_portfolio.positions
    # Shadow must have rejected due to insufficient L2 depth
    assert "BTCUSDT" not in engine.shadow_portfolio.positions
    assert len(engine.shadow_portfolio.rejected_orders) == 1
    assert "INSUFFICIENT_L2_DEPTH" in engine.shadow_portfolio.rejected_orders[0]["reason"]


# ---------------------------------------------------------------------------
# P0.5: Perpetual Funding Rate Accrual (Strictly 1x per 8-Hour Window)
# ---------------------------------------------------------------------------
def test_strict_perpetual_funding_accrual(shadow_env):
    """
    P0.5: Verifies that funding rate is accrued strictly ONCE per 8-hour window
    for held positions, and duplicate bars do not re-charge funding.
    """
    engine, db_path, state_file = shadow_env

    entry_ts = 1700000000000
    engine.process_closed_bar_event(
        exchange_ts=entry_ts,
        symbol="ETHUSDT",
        bar={
            "high": "2100.0", "low": "1990.0", "close": "2050.0",
            "upper": "2000.0", "lower": "1800.0", "atr": "20.0",
            "adx": 25.0, "ema_fast": 2040.0, "ema_slow": 1900.0,
        },
        best_bid=Decimal("2049.80"), best_ask=Decimal("2050.20"),
        depth_bid_usdt=Decimal("100000.0"), depth_ask_usdt=Decimal("100000.0"),
    )
    pos_nat = engine.native_portfolio.positions["ETHUSDT"]
    qty = pos_nat.current_qty
    rate = Decimal("0.0001")

    # Settlement 1: Exactly 8 hours later
    ts_8h = entry_ts + 8 * 3600 * 1000
    engine.process_closed_bar_event(
        exchange_ts=ts_8h,
        symbol="ETHUSDT",
        bar={
            "high": "2060.0", "low": "2040.0", "close": "2050.0",
            "upper": "2100.0", "lower": "1800.0", "atr": "20.0",
            "adx": 25.0, "ema_fast": 2040.0, "ema_slow": 1900.0,
        },
        best_bid=Decimal("2049.80"), best_ask=Decimal("2050.20"),
        depth_bid_usdt=Decimal("100000.0"), depth_ask_usdt=Decimal("100000.0"),
        funding_rate=rate,
    )
    expected_payment = -(qty * Decimal("2050.0") * rate)
    assert engine.native_portfolio.positions["ETHUSDT"].funding_pnl == expected_payment

    # Next bar 1 hour later (within SAME 8-hour funding window):
    # Passing funding_rate MUST NOT re-accrue payment!
    ts_9h = ts_8h + 3600 * 1000
    engine.process_closed_bar_event(
        exchange_ts=ts_9h,
        symbol="ETHUSDT",
        bar={
            "high": "2060.0", "low": "2040.0", "close": "2050.0",
            "upper": "2100.0", "lower": "1800.0", "atr": "20.0",
            "adx": 25.0, "ema_fast": 2040.0, "ema_slow": 1900.0,
        },
        best_bid=Decimal("2049.80"), best_ask=Decimal("2050.20"),
        depth_bid_usdt=Decimal("100000.0"), depth_ask_usdt=Decimal("100000.0"),
        funding_rate=rate,
    )
    # Payment should remain unchanged
    assert engine.native_portfolio.positions["ETHUSDT"].funding_pnl == expected_payment


# ---------------------------------------------------------------------------
# P0.4: Transactional SQLite State Journal & 5-Point Crash Recovery
# ---------------------------------------------------------------------------
def test_transactional_recovery_5_critical_points(shadow_env):
    """
    P0.4: Tests state persistence and transactional crash recovery across
    5 critical lifecycle points:
    1. Entry Fill
    2. After Cascade TP1
    3. After Cascade TP2
    4. Active stop / trailing
    5. During АКПП gear shift
    """
    engine, db_path, state_file = shadow_env

    # Point 1: Entry
    entry_ts = 1700000000000
    engine.process_closed_bar_event(
        exchange_ts=entry_ts,
        symbol="SOLUSDT",
        bar={
            "high": "101.0", "low": "99.0", "close": "100.0",
            "upper": "99.0", "lower": "90.0", "atr": "2.0",
            "adx": 22.0, "ema_fast": 101.0, "ema_slow": 95.0,
        },
        best_bid=Decimal("99.98"), best_ask=Decimal("100.02"),
        depth_bid_usdt=Decimal("50000.0"), depth_ask_usdt=Decimal("50000.0"),
    )
    # Delete disk JSON to force recovery from SQLite shadow_state_journal
    if os.path.exists(state_file):
        os.remove(state_file)

    rec_1 = V32LegacyShadowEngine(initial_capital=Decimal("300.000000"), db_path=db_path, state_file=state_file)
    assert rec_1.load_state() is True
    assert "SOLUSDT" in rec_1.shadow_portfolio.positions
    assert rec_1.shadow_portfolio.positions["SOLUSDT"].tp1_taken is False

    # Point 2: After Cascade TP1
    tp1_ts = entry_ts + 3600000
    rec_1.process_closed_bar_event(
        exchange_ts=tp1_ts,
        symbol="SOLUSDT",
        bar={
            "high": "104.0", "low": "101.0", "close": "103.5",
            "upper": "104.5", "lower": "95.0", "atr": "2.0",
            "adx": 22.0, "ema_fast": 102.0, "ema_slow": 95.0,
        },
        best_bid=Decimal("103.48"), best_ask=Decimal("103.52"),
        depth_bid_usdt=Decimal("50000.0"), depth_ask_usdt=Decimal("50000.0"),
    )
    if os.path.exists(state_file):
        os.remove(state_file)

    rec_2 = V32LegacyShadowEngine(initial_capital=Decimal("300.000000"), db_path=db_path, state_file=state_file)
    assert rec_2.load_state() is True
    pos_2 = rec_2.shadow_portfolio.positions["SOLUSDT"]
    assert pos_2.tp1_taken is True
    assert pos_2.is_breakeven_set is True

    # Point 3: After Cascade TP2
    tp2_ts = tp1_ts + 3600000
    rec_2.process_closed_bar_event(
        exchange_ts=tp2_ts,
        symbol="SOLUSDT",
        bar={
            "high": "107.5", "low": "104.0", "close": "107.0",
            "upper": "108.0", "lower": "95.0", "atr": "2.0",
            "adx": 22.0, "ema_fast": 103.0, "ema_slow": 95.0,
        },
        best_bid=Decimal("106.98"), best_ask=Decimal("107.02"),
        depth_bid_usdt=Decimal("50000.0"), depth_ask_usdt=Decimal("50000.0"),
    )
    if os.path.exists(state_file):
        os.remove(state_file)

    rec_3 = V32LegacyShadowEngine(initial_capital=Decimal("300.000000"), db_path=db_path, state_file=state_file)
    assert rec_3.load_state() is True
    pos_3 = rec_3.shadow_portfolio.positions["SOLUSDT"]
    assert pos_3.tp2_taken is True

    # Point 4: Exit by runner / stop
    runner_ts = tp2_ts + 3600000
    rec_3.process_closed_bar_event(
        exchange_ts=runner_ts,
        symbol="SOLUSDT",
        bar={
            "high": "106.0", "low": "93.0", "close": "94.0",
            "upper": "108.0", "lower": "95.0", "atr": "2.0",
            "adx": 22.0, "ema_fast": 101.0, "ema_slow": 95.0,
        },
        best_bid=Decimal("93.98"), best_ask=Decimal("94.02"),
        depth_bid_usdt=Decimal("50000.0"), depth_ask_usdt=Decimal("50000.0"),
    )
    if os.path.exists(state_file):
        os.remove(state_file)

    rec_4 = V32LegacyShadowEngine(initial_capital=Decimal("300.000000"), db_path=db_path, state_file=state_file)
    assert rec_4.load_state() is True
    assert "SOLUSDT" not in rec_4.shadow_portfolio.positions
    assert len(rec_4.shadow_portfolio.closed_trades) == 1

    # Point 5: During АКПП gear shift
    rec_4.shadow_portfolio.risk_manager.state.current_gear = 2
    rec_4.save_state()
    if os.path.exists(state_file):
        os.remove(state_file)

    rec_5 = V32LegacyShadowEngine(initial_capital=Decimal("300.000000"), db_path=db_path, state_file=state_file)
    assert rec_5.load_state() is True
    assert rec_5.shadow_gear == 2
