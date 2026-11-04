"""
Tests for Torg_Bot_3 Bridge Adapter.
Verifies seamless integration between NewDragon v4.0 Phase 1 modules and Torg_Bot_3 schemas.
"""
import sys
from pathlib import Path
import pytest

root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

from adapters.torg_bot_3_bridge import UniversalRiskBridge, AdaptiveExitBridge


def test_torg_bot_risk_bridge_sizing():
    """Verifies that the bridge formats requests from Torg_Bot_3 correctly."""
    bridge = UniversalRiskBridge()

    # Case 1: Healthy Bull trade on SOLUSDT with $300 capital
    qty, reason = bridge.size_position_for_torg_bot(
        symbol="SOLUSDT",
        side="Buy",
        entry_price=160.0,
        stop_price=156.0,  # 2.5% stop
        capital_usdt=300.0,
        available_margin_usdt=200.0,
        current_drawdown_pct=0.02,
        market_regime="bull",
        qty_step=0.1,
        min_order_qty=0.1,
        min_notional=5.0,
    )
    assert reason is None
    assert qty >= 0.1
    assert qty * 160.0 >= 5.0

    # Case 2: Drawdown >= 20% triggers emergency halt
    qty_halt, reason_halt = bridge.size_position_for_torg_bot(
        symbol="SOLUSDT",
        side="Buy",
        entry_price=160.0,
        stop_price=156.0,
        capital_usdt=300.0,
        available_margin_usdt=200.0,
        current_drawdown_pct=0.22,  # 22% DD
        market_regime="bull",
    )
    assert qty_halt == 0.0
    assert "Drawdown" in reason_halt

    # Case 3: Shock regime blocks entries
    qty_shock, reason_shock = bridge.size_position_for_torg_bot(
        symbol="SOLUSDT",
        side="Buy",
        entry_price=160.0,
        stop_price=156.0,
        capital_usdt=300.0,
        available_margin_usdt=200.0,
        current_drawdown_pct=0.01,
        market_regime="shock",
    )
    assert qty_shock == 0.0
    assert "Shock" in reason_shock


def test_torg_bot_exit_bridge_lifecycle():
    """Verifies full lifecycle of AdaptiveExitBridge for a Torg_Bot_3 position."""
    exit_bridge = AdaptiveExitBridge()

    # Open a LONG on SOLUSDT at 150 with stop at 145 (1R = $5.0)
    tracker = exit_bridge.register_position(
        symbol="SOLUSDT",
        side="Buy",
        entry_price=150.0,
        stop_price=145.0,
        qty=10.0,
    )
    assert tracker.stage == 0

    # Bar 1: Price climbs to 154.3 (+0.86R) -> Stage 1 Ratchet triggers
    act1 = exit_bridge.on_bar_update(
        symbol="SOLUSDT",
        high=154.5,
        low=151.0,
        close=154.3,
        prev_high=151.0,
        prev_low=149.0,
        highest_20=154.5,
        lowest_20=148.0,
        atr_1h=2.0,
    )
    assert act1.action_type == "UPDATE_STOP"
    assert tracker.stage == 1
    assert tracker.current_stop == pytest.approx(150.0 - 0.35 * 5.0, rel=1e-5)

    # Bar 2: Price hits 157.2 (+1.44R) -> Stage 2 Partial Scale-Out triggers
    act2 = exit_bridge.on_bar_update(
        symbol="SOLUSDT",
        high=157.5,
        low=154.0,
        close=157.2,
        prev_high=154.5,
        prev_low=151.0,
        highest_20=157.5,
        lowest_20=148.0,
        atr_1h=2.0,
    )
    assert act2.action_type == "PARTIAL_CLOSE_AND_STOP"
    assert act2.close_qty_ratio == 0.60
    assert tracker.remaining_qty == pytest.approx(4.0, rel=1e-5)
    assert tracker.stage == 2
    # Profit lock stop at +0.35R
    assert tracker.current_stop == pytest.approx(150.0 + 0.35 * 5.0, rel=1e-5)
