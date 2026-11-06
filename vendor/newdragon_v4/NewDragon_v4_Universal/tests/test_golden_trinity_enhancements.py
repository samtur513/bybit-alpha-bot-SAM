"""
Unit tests for NewDragon v4 Phase 1 Enhancements (The Golden Trinity of Profit):
1. Dynamic Stage 2 Sizing (40% in Strong Trend, 70% in Chop, 60% default).
2. MFE Pullback Breakeven (locks BE + 0.20% on >= 35% pullback after reaching >= 1.0R).
3. Portfolio Heat Guard & Correlation Cluster Protection.
"""
import sys
from pathlib import Path
import pytest

root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

from core.models import (
    InstrumentSpec,
    PortfolioState,
    SignalOutput,
    PositionTracker,
    ExitAction,
)
from layers.l6_execution.exit_fsm import AdaptiveExitFSM
from layers.l5_risk import UniversalPositionSizer
from adapters.torg_bot_3_bridge import UniversalRiskBridge, AdaptiveExitBridge


def test_dynamic_stage2_sizing():
    """Verifies that Stage 2 partial exit scales dynamically based on trend conviction."""
    fsm = AdaptiveExitFSM()

    # Case 1: Strong trend (KER/ADX > 0.25 or is_strong_trend=True) -> Closes only 40% (60% runner!)
    pos_trend = PositionTracker(
        symbol="SOLUSDT",
        side="LONG",
        entry_price=100.0,
        current_stop=98.0,
        initial_1r_dist=2.0,
        remaining_qty=10.0,
        stage=1,
    )
    act_trend = fsm.evaluate_bar(
        pos=pos_trend,
        high=103.5,
        low=102.0,
        close=103.0,  # +1.5R (> 1.4R)
        prev_high=102.0,
        prev_low=101.0,
        highest_20=103.5,
        lowest_20=99.0,
        atr_1h=1.0,
        is_strong_trend=True,
    )
    assert act_trend.action_type == "PARTIAL_CLOSE_AND_STOP"
    assert act_trend.close_qty_ratio == 0.40
    assert pos_trend.stage == 2

    # Case 2: Chop / Weak trend (trend_score = 0.05) -> Closes 70% to lock gains early
    pos_chop = PositionTracker(
        symbol="SOLUSDT",
        side="LONG",
        entry_price=100.0,
        current_stop=98.0,
        initial_1r_dist=2.0,
        remaining_qty=10.0,
        stage=1,
    )
    act_chop = fsm.evaluate_bar(
        pos=pos_chop,
        high=103.5,
        low=102.0,
        close=103.0,
        prev_high=102.0,
        prev_low=101.0,
        highest_20=103.5,
        lowest_20=99.0,
        atr_1h=1.0,
        trend_score=0.05,
    )
    assert act_chop.action_type == "PARTIAL_CLOSE_AND_STOP"
    assert act_chop.close_qty_ratio == 0.70

    # Case 3: Default baseline -> Closes standard 60%
    pos_base = PositionTracker(
        symbol="SOLUSDT",
        side="LONG",
        entry_price=100.0,
        current_stop=98.0,
        initial_1r_dist=2.0,
        remaining_qty=10.0,
        stage=1,
    )
    act_base = fsm.evaluate_bar(
        pos=pos_base,
        high=103.5,
        low=102.0,
        close=103.0,
        prev_high=102.0,
        prev_low=101.0,
        highest_20=103.5,
        lowest_20=99.0,
        atr_1h=1.0,
    )
    assert act_base.action_type == "PARTIAL_CLOSE_AND_STOP"
    assert act_base.close_qty_ratio == 0.60


def test_mfe_pullback_breakeven_protection():
    """
    Verifies that a position reaching >= +1.0R that pulls back >= 35%
    triggers an early Breakeven (+0.20%) stop update, preventing winner-to-loser turn.
    """
    fsm = AdaptiveExitFSM()
    pos = PositionTracker(
        symbol="SOLUSDT",
        side="LONG",
        entry_price=100.0,
        current_stop=99.30,  # Ratchet stop at -0.35R
        initial_1r_dist=2.0,
        remaining_qty=10.0,
        stage=1,
    )

    # Bar 1: Price rallies to high 102.4 (+1.2R) and closes at 102.2
    act1 = fsm.evaluate_bar(
        pos=pos,
        high=102.4,  # peak_r = (102.4 - 100) / 2 = 1.2R
        low=101.5,
        close=102.2,
        prev_high=101.5,
        prev_low=100.5,
        highest_20=102.4,
        lowest_20=99.0,
        atr_1h=1.0,
    )
    assert act1.action_type == "HOLD"
    assert pos.max_favor_price == 102.4

    # Bar 2: Sharp pullback to close 101.2 (unrealized R = 0.60).
    # Pullback = 1.2R - 0.6R = 0.6R (50% pullback >= 35%).
    act2 = fsm.evaluate_bar(
        pos=pos,
        high=102.0,
        low=101.0,
        close=101.2,
        prev_high=102.4,
        prev_low=101.5,
        highest_20=102.4,
        lowest_20=99.0,
        atr_1h=1.0,
    )
    assert act2.action_type == "UPDATE_STOP"
    assert act2.reason == "stage1_mfe_pullback_be"
    # Guaranteed breakeven + 0.20%
    expected_be_stop = 100.0 * 1.0020
    assert act2.new_stop == pytest.approx(expected_be_stop, rel=1e-5)
    assert pos.current_stop == pytest.approx(expected_be_stop, rel=1e-5)


def test_portfolio_heat_and_correlation_guard():
    """
    Verifies that Portfolio Heat Cap and Correlation Cluster Limits
    strictly reject or cap new trades.
    """
    bridge = UniversalRiskBridge()

    # Case 1: Healthy trade within heat capacity
    qty_ok, reason_ok = bridge.size_position_for_torg_bot(
        symbol="SOLUSDT",
        side="Buy",
        entry_price=150.0,
        stop_price=147.0,
        capital_usdt=1000.0,
        available_margin_usdt=700.0,
        current_drawdown_pct=0.01,
        current_portfolio_heat=0.02,  # 2% active heat
        max_portfolio_heat=0.045,      # 4.5% limit
        active_correlated_positions=1, # 1 active
        max_correlated_positions=2,    # max 2
    )
    assert qty_ok > 0.0
    assert reason_ok is None

    # Case 2: Max Correlated Positions reached (2 of 2) -> Rejection!
    qty_corr, reason_corr = bridge.size_position_for_torg_bot(
        symbol="AVAXUSDT",
        side="Buy",
        entry_price=30.0,
        stop_price=29.0,
        capital_usdt=1000.0,
        available_margin_usdt=700.0,
        current_drawdown_pct=0.01,
        current_portfolio_heat=0.02,
        max_portfolio_heat=0.045,
        active_correlated_positions=2,  # Already at limit!
        max_correlated_positions=2,
    )
    assert qty_corr == 0.0
    assert "Correlation cluster cap" in reason_corr

    # Case 3: Portfolio Heat Capacity exhausted (4.5% of 4.5%) -> Rejection!
    qty_heat, reason_heat = bridge.size_position_for_torg_bot(
        symbol="NEARUSDT",
        side="Buy",
        entry_price=5.0,
        stop_price=4.8,
        capital_usdt=1000.0,
        available_margin_usdt=700.0,
        current_drawdown_pct=0.01,
        current_portfolio_heat=0.046,   # Heat >= 4.5%
        max_portfolio_heat=0.045,
        active_correlated_positions=0,
    )
    assert qty_heat == 0.0
    assert "Portfolio heat limit" in reason_heat
