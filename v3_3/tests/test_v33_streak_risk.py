"""
Unit Tests for Multi-Alpha Growth Engine v3.3 Turbo.
Validates:
1. StreakRiskManager state transitions (wins step up, losses step down smoothly, scratch neutral).
2. Sizing calculations per gear (1.5%, 2.2%, 3.2%).
3. Dynamic Cascade Take-Profit R:R targets (1:3 -> 1:4 -> 1:5).
4. Trade accounting invariants: penny-perfect equity conservation.
"""

from decimal import Decimal
import pytest

from v3_3.src.streak_risk_manager import StreakRiskManager, GEAR_CONFIGS
from v3_3.src.core_v33 import CoreV33Engine
from v3.src.pyramiding_engine import ActivePosition


def test_streak_manager_initial_state():
    mgr = StreakRiskManager(initial_capital=Decimal("300.0"))
    assert mgr.current_gear == 1
    assert mgr.streak_wins == 0
    assert mgr.streak_losses == 0

    cfg = mgr.get_config()
    assert cfg.gear == 1
    assert cfg.risk_pct == Decimal("0.015")
    assert cfg.tp1_r_mult == Decimal("3.0")
    assert cfg.tp2_r_mult == Decimal("4.0")
    assert cfg.pyramiding_allowed is False


def test_streak_manager_step_up_on_winning_streak():
    mgr = StreakRiskManager(initial_capital=Decimal("300.0"))

    # Trade 1: Solid Win (+$15.00)
    g1 = mgr.record_closed_trade(net_pnl=Decimal("15.00"), initial_risk_usd=Decimal("6.00"))
    assert g1 == 2
    assert mgr.streak_wins == 1
    assert mgr.streak_losses == 0
    cfg1 = mgr.get_config()
    assert cfg1.risk_pct == Decimal("0.022")
    assert cfg1.tp1_r_mult == Decimal("3.5")
    assert cfg1.tp2_r_mult == Decimal("4.5")
    assert cfg1.pyramiding_allowed is True
    assert cfg1.pyramid_ratio == Decimal("0.50")

    # Trade 2: Second Solid Win (+$20.00) -> Gear 3
    g2 = mgr.record_closed_trade(net_pnl=Decimal("20.00"), initial_risk_usd=Decimal("6.00"))
    assert g2 == 3
    assert mgr.streak_wins == 2
    assert mgr.streak_losses == 0
    cfg2 = mgr.get_config()
    assert cfg2.risk_pct == Decimal("0.032")
    assert cfg2.tp1_r_mult == Decimal("4.0")
    assert cfg2.tp2_r_mult == Decimal("5.5")
    assert cfg2.pyramiding_allowed is True
    assert cfg2.pyramid_ratio == Decimal("0.75")

    # Trade 3: Third Solid Win -> Stays at Gear 3
    g3 = mgr.record_closed_trade(net_pnl=Decimal("30.00"), initial_risk_usd=Decimal("6.00"))
    assert g3 == 3
    assert mgr.streak_wins == 3


def test_streak_manager_smooth_step_down_on_losses():
    """Verify user rule: Gear 3 -> 1 loss -> Gear 2 (1:4) -> 2nd loss -> Gear 1 (1:3)."""
    mgr = StreakRiskManager(initial_capital=Decimal("300.0"))
    # Advance to Gear 3
    mgr.record_closed_trade(net_pnl=Decimal("15.00"), initial_risk_usd=Decimal("6.00"))
    mgr.record_closed_trade(net_pnl=Decimal("15.00"), initial_risk_usd=Decimal("6.00"))
    assert mgr.current_gear == 3

    # Loss 1: Step down to Gear 2
    g_down1 = mgr.record_closed_trade(net_pnl=Decimal("-6.00"), initial_risk_usd=Decimal("6.00"))
    assert g_down1 == 2
    assert mgr.streak_losses == 1
    assert mgr.streak_wins == 0
    assert mgr.get_config().gear == 2

    # Loss 2: Step down to Gear 1
    g_down2 = mgr.record_closed_trade(net_pnl=Decimal("-6.00"), initial_risk_usd=Decimal("6.00"))
    assert g_down2 == 1
    assert mgr.streak_losses == 2
    assert mgr.streak_wins == 0
    assert mgr.get_config().gear == 1

    # Loss 3: Clamped at Gear 1
    g_down3 = mgr.record_closed_trade(net_pnl=Decimal("-6.00"), initial_risk_usd=Decimal("6.00"))
    assert g_down3 == 1
    assert mgr.streak_losses == 3


def test_streak_manager_scratch_neutrality():
    """Verify that small scratch / breakeven trades (|net_pnl| <= 0.3*1R) do not break streaks."""
    mgr = StreakRiskManager(initial_capital=Decimal("300.0"))
    # Advance to Gear 2
    mgr.record_closed_trade(net_pnl=Decimal("15.00"), initial_risk_usd=Decimal("6.00"))
    assert mgr.current_gear == 2
    assert mgr.streak_wins == 1

    # FAST_SCRATCH: small loss (-$1.20 against $6.00 risk = 0.2R < 0.3R)
    g_scratch = mgr.record_closed_trade(net_pnl=Decimal("-1.20"), initial_risk_usd=Decimal("6.00"))
    assert g_scratch == 2
    assert mgr.streak_wins == 1
    assert mgr.streak_losses == 0

    # Small breakeven scratch (+$0.40)
    g_be = mgr.record_closed_trade(net_pnl=Decimal("0.40"), initial_risk_usd=Decimal("6.00"))
    assert g_be == 2
    assert mgr.streak_wins == 1


def test_streak_manager_drawdown_circuit_breaker():
    mgr = StreakRiskManager(
        initial_capital=Decimal("300.0"),
        circuit_breaker_dd_pct=Decimal("10.0"),
        recovery_dd_pct=Decimal("5.0"),
    )
    # Advance to Gear 3
    mgr.record_closed_trade(Decimal("15.0"), Decimal("6.0"))
    mgr.record_closed_trade(Decimal("15.0"), Decimal("6.0"))
    assert mgr.current_gear == 3

    # Normal check at 4% DD -> Gear 3
    assert mgr.get_effective_gear(current_dd=Decimal("4.0")) == 3

    # DD spikes to 12.0% -> Locks into Gear 1
    assert mgr.get_effective_gear(current_dd=Decimal("12.0")) == 1

    # DD recovers to 7.0% -> Still locked in Gear 1 (needs < 5.0%)
    assert mgr.get_effective_gear(current_dd=Decimal("7.0")) == 1

    # DD recovers to 4.5% -> Reset to internal gear
    assert mgr.get_effective_gear(current_dd=Decimal("4.5")) == 3


def test_streak_sizing_calculation():
    mgr = StreakRiskManager(initial_capital=Decimal("300.0"))
    equity = Decimal("300.0")

    # Gear 1: 1.5% risk = $4.50
    # Stop distance = $1.00 -> Qty = 4.5
    res1 = mgr.calculate_position_size(
        equity=equity,
        entry_price=Decimal("10.0"),
        stop_price=Decimal("9.0"),
        min_order_qty=Decimal("0.1"),
        qty_step=Decimal("0.1"),
    )
    assert res1.allowed is True
    assert res1.qty == Decimal("4.5")
    assert res1.effective_risk_pct == Decimal("0.015")
    assert res1.gear == 1

    # Advance to Gear 3: 3.2% risk = $9.60
    mgr.current_gear = 3
    res3 = mgr.calculate_position_size(
        equity=equity,
        entry_price=Decimal("10.0"),
        stop_price=Decimal("9.0"),
        min_order_qty=Decimal("0.1"),
        qty_step=Decimal("0.1"),
    )
    assert res3.allowed is True
    assert res3.qty == Decimal("9.6")
    assert res3.effective_risk_pct == Decimal("0.032")
    assert res3.gear == 3


def test_v33_engine_accounting_invariant():
    """Verify penny-perfect realized equity conservation in CoreV33Engine."""
    rules = {
        "SOLUSDT": {
            "minOrderQty": Decimal("0.01"),
            "qtyStep": Decimal("0.01"),
            "minNotional": Decimal("5.0"),
            "maxOrderQty": Decimal("1000.0"),
        }
    }
    engine = CoreV33Engine(
        initial_capital=Decimal("300.0"),
        instrument_rules=rules,
        enable_cascade_tp=True,
    )
    assert engine.streak_manager.current_gear == 1

    # Add a mock active position
    pos = ActivePosition(
        symbol="SOLUSDT",
        direction=1,
        initial_qty=Decimal("2.0"),
        current_qty=Decimal("2.0"),
        entry_price=Decimal("100.0"),
        current_stop=Decimal("95.0"),
        initial_atr=Decimal("2.0"),
        highest_price=Decimal("100.0"),
        lowest_price=Decimal("100.0"),
        entry_time=1000,
        original_entry=Decimal("100.0"),
        entry_fees=Decimal("0.18"),
        tp1_r_mult=Decimal("3.0"),
        tp2_r_mult=Decimal("4.0"),
        tp1_ratio=Decimal("0.35"),
        tp2_ratio=Decimal("0.35"),
    )
    engine.realized_equity -= pos.entry_fees
    engine.open_positions["SOLUSDT"] = pos

    # Simulate price reaching TP1 (+3.0R = 100 + 3*2 = 106)
    # High = 107.0 -> triggers CASCADE_TP1
    engine._manage(
        sym="SOLUSDT",
        timestamp=2000,
        high=Decimal("107.0"),
        low=Decimal("105.0"),
        close=Decimal("106.5"),
        atr=Decimal("2.0"),
        execution=Decimal("106.0"),
        prices={"SOLUSDT": Decimal("106.0")},
    )

    assert pos.tp1_taken is True
    assert pos.current_qty < Decimal("2.0")
    # Verify stop was moved to breakeven or above
    assert pos.current_stop >= Decimal("100.0")

    # Now close the rest at 108.0
    engine._reduce("SOLUSDT", pos.current_qty, Decimal("108.0"), 3000, "TAKE_PROFIT")
    assert "SOLUSDT" not in engine.open_positions
    assert len(engine.closed_trades) == 1

    closed = engine.closed_trades[0]
    assert closed.net_pnl > Decimal("0")
    # Realized equity must match initial + net_pnl
    assert engine.realized_equity == Decimal("300.0") + closed.net_pnl

    # Streak risk manager should have recorded the win and stepped up to Gear 2!
    assert engine.streak_manager.current_gear == 2
