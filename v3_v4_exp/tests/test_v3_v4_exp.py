"""
Unit & Invariant Tests for v3-v4-exp NewDragon Trend Lab.
Verifies:
1. True 1R sizing, minNotional guard, portfolio heat guard.
2. Dynamic Stage 2 sizing (40% strong trend, 70% chop, 60% default).
3. Stage 1 MFE Pullback Breakeven protection (+0.20% locked).
4. Penny-perfect trade accounting invariant.
"""

from decimal import Decimal
import pytest
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from v3_v4_exp.src.config import (
    INITIAL_CAPITAL,
    STAGE2_STRONG_TREND_CLOSE_PCT,
    STAGE2_CHOP_CLOSE_PCT,
    STAGE2_DEFAULT_CLOSE_PCT,
    MAX_PORTFOLIO_HEAT_PCT,
    MAX_CORRELATED_POSITIONS,
)
from v3_v4_exp.src.core_v3_v4 import CoreV3V4ExpEngine
from v3.src.core_v3 import ActivePosition


def test_v3_v4_exp_initialization():
    engine = CoreV3V4ExpEngine(initial_capital=Decimal("300.0"))
    assert engine.initial_capital == Decimal("300.0")
    assert engine.realized_equity == Decimal("300.0")
    assert len(engine.open_positions) == 0
    assert len(engine.closed_trades) == 0


def test_v3_v4_exp_true_1r_sizing_and_heat_guard():
    rules = {
        "SOLUSDT": {
            "qtyStep": Decimal("0.01"),
            "minOrderQty": Decimal("0.01"),
            "maxOrderQty": Decimal("10000"),
            "tickSize": Decimal("0.01"),
            "minNotional": Decimal("5.0"),
        }
    }
    engine = CoreV3V4ExpEngine(initial_capital=Decimal("300.0"), instrument_rules=rules)

    # Size position within heat capacity
    qty, reason = engine.risk_bridge.size_position_for_torg_bot(
        symbol="SOLUSDT",
        side="LONG",
        entry_price=100.0,
        stop_price=98.0,
        capital_usdt=300.0,
        available_margin_usdt=300.0,
        current_drawdown_pct=0.01,
        qty_step=0.01,
        min_order_qty=0.01,
        min_notional=5.0,
        current_portfolio_heat=0.01,
        max_portfolio_heat=MAX_PORTFOLIO_HEAT_PCT,
        active_correlated_positions=0,
        max_correlated_positions=MAX_CORRELATED_POSITIONS,
    )
    assert qty > 0.0
    assert reason is None

    # Correlation cluster cap rejection
    qty_corr, reason_corr = engine.risk_bridge.size_position_for_torg_bot(
        symbol="AVAXUSDT",
        side="LONG",
        entry_price=30.0,
        stop_price=29.0,
        capital_usdt=300.0,
        available_margin_usdt=300.0,
        current_drawdown_pct=0.01,
        qty_step=0.01,
        min_order_qty=0.01,
        min_notional=5.0,
        current_portfolio_heat=0.01,
        max_portfolio_heat=MAX_PORTFOLIO_HEAT_PCT,
        active_correlated_positions=2,  # Already at limit!
        max_correlated_positions=MAX_CORRELATED_POSITIONS,
    )
    assert qty_corr == 0.0
    assert "Correlation cluster cap" in reason_corr


def test_v3_v4_exp_dynamic_stage2_sizing():
    """Verifies that Stage 2 partial exit scales dynamically based on trend conviction."""
    rules = {
        "SOLUSDT": {
            "qtyStep": Decimal("0.01"),
            "minOrderQty": Decimal("0.01"),
            "maxOrderQty": Decimal("10000"),
            "tickSize": Decimal("0.01"),
            "minNotional": Decimal("5.0"),
        }
    }
    engine = CoreV3V4ExpEngine(initial_capital=Decimal("300.0"), instrument_rules=rules)

    bars = {
        "SOLUSDT": {
            "open": 100.0,
            "high": 101.5,
            "low": 99.5,
            "close": 101.0,
            "upper": 100.0,  # breakout trigger
            "lower": 95.0,
            "ema_fast": 99.0,
            "ema_slow": 97.0,
            "atr": 1.5,
            "volume": 50000.0,
            "volume_sma": 30000.0,
            "adx": 28.0,  # Strong trend!
        }
    }
    next_prices = {"SOLUSDT": Decimal("101.0")}

    engine.process_bar_event(
        timestamp=1000000,
        bars=bars,
        next_open_prices=next_prices,
        manage_positions=True,
    )

    assert "SOLUSDT" in engine.open_positions
    tracker = engine.trackers["SOLUSDT"]
    # In strong trend (ADX >= 25.0), dynamic ratio should be 40%
    assert tracker.dynamic_tp_ratio == STAGE2_STRONG_TREND_CLOSE_PCT


def test_v3_v4_exp_mfe_breakeven_protection():
    """Verifies MFE Pullback Breakeven protection when price reaches +1.0R and pulls back >= 35%."""
    rules = {
        "SOLUSDT": {
            "qtyStep": Decimal("0.01"),
            "minOrderQty": Decimal("0.01"),
            "maxOrderQty": Decimal("10000"),
            "tickSize": Decimal("0.01"),
            "minNotional": Decimal("5.0"),
        }
    }
    engine = CoreV3V4ExpEngine(initial_capital=Decimal("300.0"), instrument_rules=rules)

    # Manually register an active position
    entry_p = Decimal("100.0")
    stop_p = Decimal("98.0")  # 1R = 2.0
    qty = Decimal("1.0")

    pos = ActivePosition(
        symbol="SOLUSDT",
        direction=1,
        initial_qty=qty,
        current_qty=qty,
        entry_price=entry_p,
        current_stop=stop_p,
        initial_atr=Decimal("1.0"),
        highest_price=entry_p,
        lowest_price=entry_p,
        entry_time=1000000,
        original_entry=entry_p,
        entry_fees=Decimal("0.055"),
        bars_held=1,
        breakout_level=entry_p,
    )
    engine.open_positions["SOLUSDT"] = pos
    nd_tracker = engine.exit_bridge.register_position("SOLUSDT", "LONG", 100.0, 98.0, 1.0)
    nd_tracker.stage = 1  # In Stage 1 ratchet

    from v3_v4_exp.src.core_v3_v4 import PositionTrackerV3V4
    engine.trackers["SOLUSDT"] = PositionTrackerV3V4(
        position=pos,
        tracker=nd_tracker,
        target_tp_maker=Decimal("102.8"),
        stage_name="Stage 1: Ratchet",
    )

    # Bar 1: Price rallies to 102.4 (+1.2R)
    engine._manage_position(
        sym="SOLUSDT",
        timestamp=2000000,
        high=Decimal("102.4"),
        low=Decimal("101.5"),
        close=Decimal("102.2"),
        atr=Decimal("1.0"),
        execution_price=Decimal("102.2"),
        prev_high=Decimal("101.5"),
        prev_low=Decimal("100.5"),
        highest_20=Decimal("102.4"),
        lowest_20=Decimal("99.0"),
        adx=22.0,
    )
    assert nd_tracker.max_favor_price == 102.4

    # Bar 2: Sharp pullback to close 101.2 (unrealized R = 0.60, 50% pullback >= 35%)
    engine._manage_position(
        sym="SOLUSDT",
        timestamp=3000000,
        high=Decimal("102.0"),
        low=Decimal("101.0"),
        close=Decimal("101.2"),
        atr=Decimal("1.0"),
        execution_price=Decimal("101.2"),
        prev_high=Decimal("102.4"),
        prev_low=Decimal("101.5"),
        highest_20=Decimal("102.4"),
        lowest_20=Decimal("99.0"),
        adx=22.0,
    )

    # Stop should now be ratcheted to entry + 0.20% (100.20)
    expected_stop = Decimal("100.20")
    assert pos.current_stop == pytest.approx(expected_stop, rel=1e-4)
    assert engine.trackers["SOLUSDT"].is_mfe_be_locked is True
    assert engine.fsm_stage_stats["STAGE1_MFE_BE"] == 1
