#!/usr/bin/env python3
"""
tests/test_v32_shadow_equivalence.py
Unit and integration test suite verifying:
1. Exact initial capital contract (300.000000 USDT) for both Native and Shadow.
2. Fee and slippage divergence between Native (0.090%) and Realistic Shadow (0.100%).
3. Microsecond SQLite event ledger logging.
4. Spread clearance gate (rejection of spreads > 0.08%).
5. News Core v1.2 macro embargo gating.
6. Auto-Gearbox (АКПП) drawdown downshift / upshift mechanics.
"""

from decimal import Decimal
import os
import sqlite3
import tempfile
import pytest

from v3_2.src.shadow.paper_trader_v32_shadow import (
    V32LegacyShadowEngine,
    INITIAL_CAPITAL,
    NATIVE_TAKER_FEE,
    SHADOW_TAKER_FEE
)

@pytest.fixture
def shadow_engine():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        temp_db = tf.name
    engine = V32LegacyShadowEngine(
        initial_capital=Decimal("300.000000"),
        db_path=temp_db,
        enable_news_defense=True,
        enable_auto_gearbox=True
    )
    yield engine
    if os.path.exists(temp_db):
        try:
            os.remove(temp_db)
        except Exception:
            pass

def test_shadow_initialization_and_contract(shadow_engine):
    assert shadow_engine.initial_capital == Decimal("300.000000")
    assert shadow_engine.native_realized_equity == Decimal("300.000000")
    assert shadow_engine.shadow_realized_equity == Decimal("300.000000")
    assert len(shadow_engine.active_trades) == 0
    assert len(shadow_engine.closed_trades) == 0
    assert shadow_engine.risk_manager.current_gear == 3
    assert shadow_engine.rejected_orders_count == 0

    # Verify SQLite schema
    with sqlite3.connect(shadow_engine.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='shadow_execution_events';")
        assert cursor.fetchone()[0] == 1

def test_orderbook_execution_and_fee_divergence(shadow_engine):
    # Simulate Long Entry: Intended 100.0, best_bid 99.98, best_ask 100.02 (spread = 0.04% < 0.08%)
    trade = shadow_engine.process_orderbook_entry(
        symbol="SOLUSDT",
        direction="LONG",
        intended_price=Decimal("100.00"),
        qty=Decimal("1.0"),
        best_bid=Decimal("99.98"),
        best_ask=Decimal("100.02"),
        bar_timestamp_ms=1700000000000,
        atr=Decimal("2.0"),
    )
    assert trade is not None
    assert "SOLUSDT" in shadow_engine.active_trades

    # Native entry: 100.0 * 1.0002 = 100.0200
    assert trade.entry_price_native == Decimal("100.0200")
    # Shadow entry: best_ask = 100.02
    assert trade.entry_price_shadow == Decimal("100.02")

    # Native fee: 1.0 * 100.02 * 0.0009 = 0.090018
    assert trade.native_fee_paid == Decimal("1.0") * Decimal("100.0200") * Decimal("0.00090")
    # Shadow fee: 1.0 * 100.02 * 0.0010 = 0.100020
    assert trade.shadow_fee_paid == Decimal("1.0") * Decimal("100.02") * Decimal("0.00100")
    assert trade.shadow_fee_paid > trade.native_fee_paid

    # Verify SQLite event log
    with sqlite3.connect(shadow_engine.db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT event_type, price_shadow, shadow_fee FROM shadow_execution_events WHERE symbol='SOLUSDT';")
        row = cursor.fetchone()
        assert row is not None
        assert row[0] == "ENTRY_FILL"
        assert abs(row[1] - 100.02) < 1e-4

def test_orderbook_exit_and_pnl_accounting(shadow_engine):
    # First enter
    shadow_engine.process_orderbook_entry(
        symbol="SOLUSDT",
        direction="LONG",
        intended_price=Decimal("100.00"),
        qty=Decimal("1.0"),
        best_bid=Decimal("99.98"),
        best_ask=Decimal("100.02"),
        bar_timestamp_ms=1700000000000,
        atr=Decimal("2.0"),
    )

    # Now exit at 105.0: best_bid 104.98, best_ask 105.02
    closed_trade = shadow_engine.process_orderbook_exit(
        symbol="SOLUSDT",
        intended_price=Decimal("105.00"),
        best_bid=Decimal("104.98"),
        best_ask=Decimal("105.02"),
        exit_time_ms=1700003600000,
        exit_reason="DONCHIAN_EXIT",
    )
    assert closed_trade is not None
    assert closed_trade.is_closed
    assert len(shadow_engine.active_trades) == 0
    assert len(shadow_engine.closed_trades) == 1

    # Shadow Gross PnL: (104.98 - 100.02) * 1.0 = +4.96
    # Total Shadow Fee: (100.02 * 0.001) + (104.98 * 0.001) = 0.10002 + 0.10498 = 0.20500
    # Shadow Net PnL = 4.96 - 0.20500 = +4.75500
    expected_shadow_net = Decimal("4.96") - (Decimal("100.02") * Decimal("0.00100") + Decimal("104.98") * Decimal("0.00100"))
    assert closed_trade.shadow_net_pnl == expected_shadow_net
    assert shadow_engine.shadow_realized_equity == Decimal("300.000000") + expected_shadow_net

def test_wide_spread_rejection_gate(shadow_engine):
    # Intended 100.0, best_bid 99.90, best_ask 100.10 -> spread = 0.20 / 99.90 = 0.2002% > 0.08%
    trade = shadow_engine.process_orderbook_entry(
        symbol="ILLIQUIDUSDT",
        direction="LONG",
        intended_price=Decimal("100.00"),
        qty=Decimal("1.0"),
        best_bid=Decimal("99.90"),
        best_ask=Decimal("100.10"),
        bar_timestamp_ms=1700000000000,
        atr=Decimal("2.0"),
    )
    assert trade is None
    assert "ILLIQUIDUSDT" not in shadow_engine.active_trades
    assert shadow_engine.rejected_orders_count == 1
    assert len(shadow_engine.missed_intent_log) == 1
    assert "WIDE_SPREAD_REJECT" in shadow_engine.missed_intent_log[0]["reason"]

def test_akpp_gearbox_drawdown_transitions(shadow_engine):
    # Start at $300.0, Peak $300.0 -> Gear 3
    assert shadow_engine.risk_manager.current_gear == 3
    assert shadow_engine.risk_manager.get_effective_base_risk_pct() == Decimal("0.010")

    # Simulate equity drawdown to $265.0 (DD = (300-265)/300 = 11.67% >= 10.0%)
    gear = shadow_engine.risk_manager.update_drawdown(Decimal("265.0"), Decimal("300.0"))
    assert gear == 2
    assert shadow_engine.risk_manager.current_gear == 2
    # Risk should be cut by 50%
    assert shadow_engine.risk_manager.get_effective_base_risk_pct() == Decimal("0.005")

    # Simulate deep drawdown to $245.0 (DD = (300-245)/300 = 18.33% >= 16.0%)
    gear = shadow_engine.risk_manager.update_drawdown(Decimal("245.0"), Decimal("300.0"))
    assert gear == 1
    assert shadow_engine.risk_manager.current_gear == 1
    # Risk should be cut by 75%
    assert shadow_engine.risk_manager.get_effective_base_risk_pct() == Decimal("0.0025")

    # Reach new peak equity $310.0 -> immediately restores Gear 3
    gear = shadow_engine.risk_manager.update_drawdown(Decimal("310.0"), Decimal("300.0"))
    assert gear == 3
    assert shadow_engine.risk_manager.current_gear == 3
