"""
Unit Tests for Core Engine v2.0 (SPEC_V2.md).
Tests Fractional Kelly sizing, Pyramiding triggers, Multi-Coin ranking, and Regime Kill-Switch.
"""

from decimal import Decimal
import pytest

from src.kelly_sizing import KellyRiskManager
from src.multi_scanner import MultiCoinScanner, CoinCandidate
from src.pyramiding_engine import PyramidingEngine, ActivePosition
from src.core_v2 import CoreV2Engine


def test_kelly_sizing_basic_calculation():
    """Verify Half-Kelly allocates 2.5% risk by default."""
    km = KellyRiskManager(base_risk_pct=Decimal("0.025"), max_leverage=Decimal("3.5"))
    equity = Decimal("300.0")
    entry_p = Decimal("3000.0")
    stop_p = Decimal("2850.0")  # distance = 150
    # Expected risk = 300 * 0.025 = $7.50
    # Expected qty = 7.50 / 150 = 0.05 ETH

    res = km.calculate_position_size(
        equity=equity,
        entry_price=entry_p,
        stop_price=stop_p,
        min_order_qty=Decimal("0.01"),
        qty_step=Decimal("0.01"),
        min_notional=Decimal("5.0"),
    )
    assert res.allowed is True
    assert res.qty == Decimal("0.05")
    assert res.notional_value == Decimal("150.0")


def test_kelly_kill_switch_trigger_and_recovery():
    """Verify 3 consecutive losses cut risk to 0.75%, and 2 wins recover risk to 2.5%."""
    km = KellyRiskManager(
        base_risk_pct=Decimal("0.025"),
        choppy_risk_pct=Decimal("0.0075"),
        loss_streak_threshold=3,
        win_streak_recovery=2,
    )
    assert km.state.is_kill_switch_active is False

    # 1 win, then 2 losses -> kill switch still inactive
    km.record_trade_result(True)
    km.record_trade_result(False)
    km.record_trade_result(False)
    assert km.state.is_kill_switch_active is False

    # 3rd consecutive loss -> activates kill switch
    km.record_trade_result(False)
    assert km.state.is_kill_switch_active is True

    # Check that sizing uses choppy risk 0.75%
    equity = Decimal("300.0")
    entry_p = Decimal("100.0")
    stop_p = Decimal("90.0")  # dist = 10
    # Risk budget = 300 * 0.0075 = $2.25 -> qty = 2.25 / 10 = 0.22 (floored to step 0.01)
    res = km.calculate_position_size(
        equity=equity,
        entry_price=entry_p,
        stop_price=stop_p,
        min_order_qty=Decimal("0.01"),
        qty_step=Decimal("0.01"),
        min_notional=Decimal("5.0"),
    )
    assert res.effective_risk_pct == Decimal("0.0075")
    assert res.qty == Decimal("0.22")

    # 1 win -> still active
    km.record_trade_result(True)
    assert km.state.is_kill_switch_active is True

    # 2nd win -> recovered!
    km.record_trade_result(True)
    assert km.state.is_kill_switch_active is False


def test_pyramiding_breakeven_and_addon():
    """Verify position moves stop to breakeven and adds 50% size at +2 ATR."""
    pe = PyramidingEngine(
        pyramid_trigger_atr_mult=Decimal("2.0"),
        pyramid_add_ratio=Decimal("0.5"),
        trailing_atr_mult=Decimal("2.5"),
    )

    pos = ActivePosition(
        symbol="ETHUSDT",
        direction=1,
        initial_qty=Decimal("0.10"),
        current_qty=Decimal("0.10"),
        entry_price=Decimal("2500.0"),
        current_stop=Decimal("2400.0"),  # 2.5 * ATR (ATR = 40)
        initial_atr=Decimal("40.0"),
        highest_price=Decimal("2500.0"),
        lowest_price=Decimal("2500.0"),
        entry_time=1000,
        is_pyramided=False,
        is_breakeven_set=False,
    )

    # Price moves to +1 ATR (2540) -> no pyramid
    action1 = pe.evaluate_bar(
        pos=pos,
        current_high=Decimal("2540.0"),
        current_low=Decimal("2495.0"),
        current_close=Decimal("2535.0"),
        current_atr=Decimal("40.0"),
        qty_step=Decimal("0.01"),
        min_order_qty=Decimal("0.01"),
    )
    assert action1.action_type != "PYRAMID_ADD"
    assert pos.is_pyramided is False

    # Price moves to +2.1 ATR (2585) -> triggers pyramiding add + breakeven
    action2 = pe.evaluate_bar(
        pos=pos,
        current_high=Decimal("2585.0"),
        current_low=Decimal("2530.0"),
        current_close=Decimal("2580.0"),
        current_atr=Decimal("40.0"),
        qty_step=Decimal("0.01"),
        min_order_qty=Decimal("0.01"),
    )
    assert action2.action_type == "PYRAMID_ADD"
    assert action2.added_qty == Decimal("0.05")  # 50% of 0.10
    assert pos.current_qty == Decimal("0.15")
    assert pos.is_pyramided is True
    assert pos.is_breakeven_set is True
    # Stop moved above entry price to protect capital
    assert pos.current_stop > Decimal("2500.0")


def test_multi_scanner_ranking_and_correlation_limit():
    """Verify candidates are ranked by TMS and directional limit is enforced."""
    scanner = MultiCoinScanner(max_concurrent_positions=2, min_volume_ratio=1.1)

    c1 = CoinCandidate(
        symbol="SOLUSDT",
        direction=1,
        close_price=Decimal("150.0"),
        breakout_boundary=Decimal("148.0"),
        atr=Decimal("4.0"),
        trend_momentum_score=8.5,
        volume_ratio=1.5,
        normalized_breakout_dist=0.5,
    )
    c2 = CoinCandidate(
        symbol="NEARUSDT",
        direction=1,
        close_price=Decimal("5.0"),
        breakout_boundary=Decimal("4.8"),
        atr=Decimal("0.2"),
        trend_momentum_score=12.0,  # higher score
        volume_ratio=2.0,
        normalized_breakout_dist=1.0,
    )
    c3 = CoinCandidate(
        symbol="AVAXUSDT",
        direction=1,
        close_price=Decimal("30.0"),
        breakout_boundary=Decimal("29.0"),
        atr=Decimal("1.0"),
        trend_momentum_score=5.0,
        volume_ratio=1.2,
        normalized_breakout_dist=1.0,
    )

    # When 0 open longs: top ranked should be NEAR (12.0), then SOL (8.5), then AVAX (5.0)
    ranked = scanner.rank_candidates([c1, c2, c3], [], current_long_count=0, current_short_count=0)
    assert len(ranked) == 3
    assert ranked[0].symbol == "NEARUSDT"
    assert ranked[1].symbol == "SOLUSDT"

    # When already 2 longs open (max=2): all new longs should be blocked
    blocked = scanner.rank_candidates([c1, c2, c3], [], current_long_count=2, current_short_count=0)
    assert len(blocked) == 0


def test_core_v2_engine_lifecycle():
    """Verify full CoreV2Engine processes bars, records equity and calculates metrics."""
    engine = CoreV2Engine(
        initial_capital=Decimal("300.0"),
        base_risk_pct=Decimal("0.025"),
        max_leverage=Decimal("3.5"),
    )

    bars = {
        "ETHUSDT": {
            "close": 3000.0,
            "high": 3050.0,
            "low": 2980.0,
            "upper": 2950.0,  # breakout above 2950
            "lower": 2800.0,
            "ema_fast": 2920.0,
            "ema_slow": 2850.0,
            "atr": 40.0,
            "volume": 2000.0,
            "volume_sma": 1000.0,  # 2.0x volume ratio
        }
    }
    next_opens = {"ETHUSDT": Decimal("3000.0")}

    # Process bar -> triggers entry
    engine.process_bar_event(1000000, bars, next_opens)
    assert "ETHUSDT" in engine.open_positions
    pos = engine.open_positions["ETHUSDT"]
    assert pos.direction == 1
    assert pos.initial_qty > Decimal("0")

    # Get snapshot
    snap = engine.get_portfolio_snapshot({"ETHUSDT": Decimal("3000.0")})
    assert snap.equity > Decimal("295.0")  # slight fee deducted
    assert snap.max_drawdown_pct < Decimal("5.0")
