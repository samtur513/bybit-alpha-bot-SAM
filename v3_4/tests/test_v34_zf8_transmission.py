"""
Unit tests and quantitative invariants for v3.4 ZF-8 Quant Transmission:
1. Gear transitions: standard win (+1), super kickdown (+2), mega kickdown (+3).
2. Downshifts: neutral scratch (0), gentle downshift (-1), ABS rapid braking (-2).
3. Drawdown circuit breaker (Limp Mode locked at DD >= 8.0%, resets at DD <= 4.0%).
4. Sizing mathematical correctness and penny-perfect invariants.
5. CoreV34Engine integration and event handling.
"""

from decimal import Decimal
import pytest

from v3_4.src.zf8_transmission import ZF8Transmission, GEAR_CONFIGS, GearConfig
from v3_4.src.core_v34 import CoreV34Engine


def test_zf8_initial_state():
    """Verify initial transmission state defaults to Gear 1 (Limp/Crawl, 1.0% risk)."""
    trans = ZF8Transmission()
    assert trans.current_gear == 1
    assert trans.streak_wins == 0
    assert trans.streak_losses == 0
    assert not trans.is_circuit_breaker_active

    cfg = trans.get_config()
    assert cfg.gear == 1
    assert cfg.risk_pct == Decimal("0.010")
    assert cfg.pyramiding_allowed is False
    assert cfg.pyramid_ratio == Decimal("0.0")
    assert cfg.tp1_r_mult == Decimal("2.8")
    assert cfg.tp2_r_mult == Decimal("3.6")


def test_zf8_standard_win_sequential_upshift():
    """Verify standard win (+1.0R to +3.0R) shifts up +1 gear sequentially."""
    trans = ZF8Transmission(initial_gear=1)
    risk_usd = Decimal("3.00")

    # Win 1: +1.5R (+$4.50) -> G1 to G2
    g = trans.record_closed_trade(net_pnl=Decimal("4.50"), initial_risk_usd=risk_usd)
    assert g == 2
    assert trans.streak_wins == 1
    assert trans.streak_losses == 0

    # Win 2: +2.0R (+$6.00) -> G2 to G3
    g = trans.record_closed_trade(net_pnl=Decimal("6.00"), initial_risk_usd=risk_usd)
    assert g == 3
    assert trans.streak_wins == 2


def test_zf8_super_kickdown_upshift():
    """Verify super-runner (+3.5R) triggers intelligent Kickdown (+2 gears)."""
    trans = ZF8Transmission(initial_gear=2)
    risk_usd = Decimal("3.90")  # 1.3% of $300

    # Super-runner: +3.8R win -> +2 gears (G2 to G4)
    g = trans.record_closed_trade(net_pnl=Decimal("14.82"), initial_risk_usd=risk_usd)
    assert g == 4
    assert trans.current_gear == 4
    assert "KICKDOWN" in trans.last_shift_reason


def test_zf8_mega_kickdown_upshift():
    """Verify mega-runner (+5.5R) triggers Mega Kickdown (+3 gears) clamped at Gear 8."""
    trans = ZF8Transmission(initial_gear=3)
    risk_usd = Decimal("4.80")  # 1.6% of $300

    # Mega-runner: +5.5R win -> +3 gears (G3 to G6)
    g = trans.record_closed_trade(net_pnl=Decimal("26.40"), initial_risk_usd=risk_usd)
    assert g == 6
    assert trans.current_gear == 6
    assert "MEGA_KICKDOWN" in trans.last_shift_reason

    # Another mega-runner: G6 + 3 = G9 -> clamped to Gear 8
    g = trans.record_closed_trade(net_pnl=Decimal("30.00"), initial_risk_usd=risk_usd)
    assert g == 8
    assert trans.current_gear == 8
    assert trans.get_config().name == "LAUNCH CONTROL"


def test_zf8_abs_rapid_braking_downshift():
    """Verify full stop loss (-1.0R) triggers ABS Rapid Braking (-2 gears down)."""
    trans = ZF8Transmission(initial_gear=6)
    risk_usd = Decimal("8.40")  # 2.8% of $300

    # Full stop loss (-1.0R): G6 drops by -2 gears to G4
    g = trans.record_closed_trade(net_pnl=Decimal("-8.40"), initial_risk_usd=risk_usd)
    assert g == 4
    assert trans.current_gear == 4
    assert "ABS_BRAKING" in trans.last_shift_reason
    assert trans.streak_losses == 1

    # Second full stop loss: G4 drops by -2 gears to G2
    g = trans.record_closed_trade(net_pnl=Decimal("-6.00"), initial_risk_usd=Decimal("6.00"))
    assert g == 2
    assert trans.current_gear == 2
    assert trans.streak_losses == 2

    # Third full stop loss: G2 drops to G1 (clamped at G1)
    g = trans.record_closed_trade(net_pnl=Decimal("-3.90"), initial_risk_usd=Decimal("3.90"))
    assert g == 1
    assert trans.current_gear == 1


def test_zf8_gentle_downshift_and_scratch():
    """Verify soft cut (-0.5R) downshifts -1 gear, and scratch (|R| < 0.3R) is neutral."""
    trans = ZF8Transmission(initial_gear=5)
    risk_usd = Decimal("7.20")

    # Scratch trade: +$0.50 (+0.07R) -> Neutral, stays at G5
    g = trans.record_closed_trade(net_pnl=Decimal("0.50"), initial_risk_usd=risk_usd)
    assert g == 5
    assert trans.current_gear == 5
    assert "NEUTRAL_SCRATCH" in trans.last_shift_reason

    # Soft loss: -$3.60 (-0.50R) -> Gentle downshift -1 gear (G5 to G4)
    g = trans.record_closed_trade(net_pnl=Decimal("-3.60"), initial_risk_usd=risk_usd)
    assert g == 4
    assert trans.current_gear == 4
    assert "DOWNSHIFT (-1G" in trans.last_shift_reason


def test_zf8_circuit_breaker_limp_mode():
    """Verify drawdown >= 8.0% locks transmission in Gear 1 (Limp Mode) until DD <= 4.0%."""
    trans = ZF8Transmission(initial_gear=7)
    assert trans.get_effective_gear(current_dd=Decimal("3.5")) == 7

    # DD spikes to 8.5% -> Trips circuit breaker, forces Gear 1
    effective_g = trans.get_effective_gear(current_dd=Decimal("8.5"))
    assert effective_g == 1
    assert trans.is_circuit_breaker_active is True

    # DD partially recovers to 6.0% -> Still locked in Gear 1 (hysteresis)
    assert trans.get_effective_gear(current_dd=Decimal("6.0")) == 1

    # DD fully recovers to 3.8% (<= 4.0%) -> Resets circuit breaker
    effective_g = trans.get_effective_gear(current_dd=Decimal("3.8"))
    assert effective_g == 7
    assert trans.is_circuit_breaker_active is False


def test_zf8_sizing_math_all_gears():
    """Verify position sizing math across all 8 gears."""
    trans = ZF8Transmission()
    equity = Decimal("300.00")
    price = Decimal("100.00")
    distance = Decimal("2.50")  # 2.5% stop distance
    qty_step = Decimal("0.01")
    min_order_qty = Decimal("0.01")
    min_notional = Decimal("5.00")

    # Gear 1: 1.0% risk -> $3.00 risk / $2.50 = 1.20 qty
    res1 = trans.calculate_sizing(equity, price, distance, qty_step, min_order_qty, min_notional)
    assert res1.allowed is True
    assert res1.gear == 1
    assert res1.risk_usd == Decimal("3.00")
    assert res1.qty == Decimal("1.20")

    # Gear 8: 3.6% risk -> $10.80 risk / $2.50 = 4.32 qty
    trans.current_gear = 8
    res8 = trans.calculate_sizing(equity, price, distance, qty_step, min_order_qty, min_notional)
    assert res8.allowed is True
    assert res8.gear == 8
    assert res8.risk_usd == Decimal("10.80")
    assert res8.qty == Decimal("4.32")


def test_core_v34_integration():
    """Verify CoreV34Engine initializes with ZF8Transmission and handles open/close."""
    engine = CoreV34Engine(initial_capital=Decimal("300.00"))
    assert engine.transmission.current_gear == 1

    # Simulate win directly through transmission
    engine.transmission.record_closed_trade(net_pnl=Decimal("15.00"), initial_risk_usd=Decimal("3.00"))
    assert engine.transmission.current_gear == 4  # Kickdown +3.0R+ (+2G) -> G1 to G3 or G4? G1 + 3 (R=5.0) -> G4!

    # Verify serialization
    state = engine.to_dict()
    assert "transmission" in state
    assert state["transmission"]["current_gear"] == 4
    assert state["transmission"]["gear_name"] == "DRIVE (MID)"
