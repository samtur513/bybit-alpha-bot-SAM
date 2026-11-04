"""
Unit and Invariant Tests for v3.8 NewDragon Trend Lab.
Verifies:
1. UniversalRiskBridge True 1R Sizing and minNotional Protection.
2. AdaptiveExitBridge 4-Stage State Machine Transitions.
3. Maker Limit Execution & Penny-Perfect Accounting Conservation on Stage 2 TP.
"""

from decimal import Decimal
from pathlib import Path
import sys
import pytest
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
VENDOR_ND4 = REPO_ROOT / "vendor" / "newdragon_v4" / "NewDragon_v4_Universal"
if str(VENDOR_ND4) not in sys.path:
    sys.path.insert(0, str(VENDOR_ND4))

from adapters.torg_bot_3_bridge import UniversalRiskBridge, AdaptiveExitBridge
from v3_8.src.core_v38 import CoreV38Engine
from v3_8.src.config import INITIAL_CAPITAL, TAKER_FEE, MAKER_FEE, SLIPPAGE


def test_universal_risk_bridge_min_notional_rejection():
    """Verify that micro-accounts reject positions if 1R risk notional < minNotional."""
    bridge = UniversalRiskBridge(taker_fee=0.0009, maker_fee=0.000324, slippage_base=0.0005, max_leverage=3.5)
    
    # On $100 capital, 0.5% risk budget = $0.50
    # Stop distance on BTC is $500. Loss per BTC = $500.
    # Qty = 0.50 / 500 = 0.001 BTC.
    # Notional = 0.001 * 80,000 = $80.
    # But Bybit BTC minNotional is $100.
    # Sizer MUST reject with qty=0.0 (NEVER expand risk to meet minNotional!).
    qty, reason = bridge.size_position_for_torg_bot(
        symbol="BTCUSDT",
        side="LONG",
        entry_price=80000.0,
        stop_price=79500.0,
        capital_usdt=100.0,
        available_margin_usdt=100.0,
        current_drawdown_pct=0.0,
        market_regime="bull",
        qty_step=0.001,
        min_order_qty=0.001,
        min_notional=100.0,
        max_leverage=3.5,
    )
    assert qty == 0.0
    assert reason is not None
    assert "minNotional" in reason or "exhausted" in reason


def test_adaptive_exit_bridge_4_stage_fsm():
    """Verify 4-stage FSM transitions: Stage 0 -> Stage 1 -> Stage 2 -> Stage 3."""
    bridge = AdaptiveExitBridge()
    # Register 1 BTC Long at $80,000, stop at $79,000 (1R = $1,000)
    tracker = bridge.register_position(
        symbol="BTCUSDT",
        side="LONG",
        entry_price=80000.0,
        stop_price=79000.0,
        qty=1.0,
    )
    assert tracker.stage == 0

    # 1. Price moves to +0.85R ($80,850) -> Stage 1 Ratchet Stop (-0.35R = $79,650)
    act1 = bridge.on_bar_update(
        symbol="BTCUSDT",
        high=80860.0,
        low=80100.0,
        close=80850.0,
        prev_high=80200.0,
        prev_low=80000.0,
        highest_20=80860.0,
        lowest_20=79000.0,
        atr_1h=500.0,
    )
    assert act1.action_type == "UPDATE_STOP"
    assert act1.new_stop == pytest.approx(79650.0, abs=1.0)
    assert tracker.stage == 1

    # 2. Price moves to +1.40R ($81,400) -> Stage 2 Partial Take Profit 60% + Lock (+0.35R = $80,350)
    act2 = bridge.on_bar_update(
        symbol="BTCUSDT",
        high=81450.0,
        low=80800.0,
        close=81400.0,
        prev_high=80860.0,
        prev_low=80100.0,
        highest_20=81450.0,
        lowest_20=79000.0,
        atr_1h=500.0,
    )
    assert act2.action_type == "PARTIAL_CLOSE_AND_STOP"
    assert act2.close_qty_ratio == pytest.approx(0.60, abs=0.01)
    assert act2.new_stop == pytest.approx(80350.0, abs=1.0)
    assert tracker.stage == 2
    assert tracker.remaining_qty == pytest.approx(0.40, abs=0.01)

    # 3. Price moves to +2.00R ($82,000) -> Stage 3 Chandelier Trailing Stop
    # Highest 20 = $82,200. Mult = 2.5 * ATR (500) = 1,250. Cand stop = 82,200 - 1,250 = 80,950
    act3 = bridge.on_bar_update(
        symbol="BTCUSDT",
        high=82200.0,
        low=81300.0,
        close=82000.0,
        prev_high=81450.0,
        prev_low=80800.0,
        highest_20=82200.0,
        lowest_20=79000.0,
        atr_1h=500.0,
    )
    assert act3.action_type == "UPDATE_STOP"
    assert act3.new_stop == pytest.approx(80950.0, abs=1.0)
    assert tracker.stage == 3


def test_core_v38_maker_tp_and_accounting_conservation():
    """Verify CoreV38Engine executes Maker limit partial TP and maintains penny-perfect conservation."""
    engine = CoreV38Engine(
        initial_capital=Decimal("300.0"),
        instrument_rules={
            "NEARUSDT": {
                "qtyStep": Decimal("0.1"),
                "minOrderQty": Decimal("0.1"),
                "minNotional": Decimal("5.0"),
                "maxOrderQty": Decimal("100000"),
            }
        },
        taker_fee=Decimal("0.0009"),
        maker_fee=Decimal("0.000324"),
        slippage=Decimal("0.0005"),
    )

    # Simulate entering LONG NEARUSDT at 4.00, stop at 3.80 (1R = 0.20)
    # Target Maker TP = 4.00 + 1.40 * 0.20 = 4.28
    prices = {"NEARUSDT": Decimal("4.00")}
    bars = {
        "NEARUSDT": {
            "open": 4.00,
            "high": 4.05,
            "low": 3.98,
            "close": 4.02,
            "atr": 0.10,
            "upper": 4.00,
            "lower": 3.50,
            "ema_fast": 4.01,
            "ema_slow": 3.90,
            "adx": 25.0,
            "volume": 10000.0,
            "volume_sma": 5000.0,
        }
    }
    next_open = {"NEARUSDT": Decimal("4.02")}

    engine.process_bar_event(
        timestamp=1000000,
        bars=bars,
        next_open_prices=next_open,
        btc_bullish=True,
        btc_bearish=False,
    )

    assert "NEARUSDT" in engine.open_positions
    pos = engine.open_positions["NEARUSDT"]
    tracker = engine.trackers["NEARUSDT"]
    initial_qty = pos.initial_qty
    entry_fees = pos.entry_fees

    # Initial conservation check
    unrealized = pos.current_qty * (next_open["NEARUSDT"] - pos.entry_price) - pos.current_qty * next_open["NEARUSDT"] * engine.taker_fee
    assert engine.get_equity(next_open) == pytest.approx(engine.realized_equity + unrealized, abs=1e-5)

    # Next bar: Price surges to high 4.35 (hitting Maker TP at ~4.302)
    bars_tp = {
        "NEARUSDT": {
            "open": 4.05,
            "high": 4.35,
            "low": 4.04,
            "close": 4.25,
            "atr": 0.10,
            "prev_high": 4.05,
            "prev_low": 3.98,
            "highest_20": 4.35,
            "lowest_20": 3.50,
        }
    }
    next_open_tp = {"NEARUSDT": Decimal("4.25")}

    engine.process_bar_event(
        timestamp=2000000,
        bars=bars_tp,
        next_open_prices=next_open_tp,
        btc_bullish=True,
        btc_bearish=False,
    )

    # 60% partial close must be executed
    assert tracker.is_tp_filled is True
    assert pos.partial_taken is True
    assert pos.current_qty == pytest.approx(initial_qty * Decimal("0.40"), abs=0.1)

    # Trailing stop must be moved to guaranteed profit lock (+0.35R)
    assert pos.current_stop > pos.entry_price

    # Verify accounting conservation
    eq_now = engine.get_equity(next_open_tp)
    unrealized_now = pos.current_qty * (next_open_tp["NEARUSDT"] - pos.entry_price) - pos.current_qty * next_open_tp["NEARUSDT"] * engine.taker_fee
    assert eq_now == pytest.approx(engine.realized_equity + unrealized_now, abs=1e-5)
