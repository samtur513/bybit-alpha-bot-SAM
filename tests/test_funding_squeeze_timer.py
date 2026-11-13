"""
Comprehensive Unit Tests for Funding Squeeze Event-Driven Timer and Execution Invariants.
FLEET-DATA-FIRST-007 Step 1.3.

Covers:
1. Event-driven entry evaluation using actual exchange nextFundingTime.
2. Rejection of early entry (time_to_settle > 1800s) and late entry (time_to_settle < 60s).
3. Rejection of low funding rate (|FR| < 0.03%) and stale market events (> 5000 ms).
4. Dynamic Breakeven Ratchet at +0.6 ATR gain.
5. Mandatory Clearing Eject at T - 1.5s (ms_until_settle <= 1500 ms).
6. Pre-settlement reconnect scenario (T - 5s reconnect preserving position and ejecting cleanly).
"""

from decimal import Decimal
import pytest
from typing import Dict, Any

from tools.fleet_unified_shadow_paper import (
    FundingSqueezeStrategy,
    ActivePaperPosition,
    OrderSide,
    OrderType,
)


def test_funding_squeeze_event_entry_in_window():
    strat = FundingSqueezeStrategy(initial_capital=Decimal("300.0"))
    settle_ts = 1720000000000  # Example settlement timestamp
    # Event occurs at T - 600s (10 minutes before settlement -> inside [60s : 1800s])
    event_ts = settle_ts - (600 * 1000)

    # 1. Positive funding -> Short Squeeze (crowded longs paying shorts)
    event_pos = {
        "symbol": "BTCUSDT",
        "timestamp": event_ts,
        "fundingRate": 0.0005,  # +0.05% per 8h (>= 0.03% threshold)
        "nextFundingTime": settle_ts,
        "mark_price": 60000.0,
        "vwap": 60050.0,  # Price below VWAP -> flow confirmation for short
        "atr_15m": 300.0,
    }
    intents = strat.evaluate_market_event(
        event=event_pos,
        open_positions={},
        current_equity=Decimal("300.0"),
        run_id="TEST_RUN",
        current_replay_ts_ms=event_ts,
    )
    assert len(intents) == 1
    intent = intents[0]
    assert intent.side == OrderSide.SELL
    assert intent.requested_qty > Decimal("0")
    assert intent.signal_mid_price == Decimal("60000.0")
    assert "FUNDING_SHORT_SQUEEZE" in intent.intent_reason
    # Causality checks
    assert intent.signal_ts_ms == event_ts + 2
    assert intent.data_received_ts_ms == event_ts + 4
    assert intent.order_submit_ts_ms == event_ts + 6

    # 2. Negative funding -> Long Squeeze (crowded shorts paying longs)
    event_neg = {
        "symbol": "ETHUSDT",
        "timestamp": event_ts,
        "fundingRate": -0.0004,  # -0.04% per 8h
        "nextFundingTime": settle_ts,
        "mark_price": 3000.0,
        "vwap": 2990.0,  # Price above VWAP -> flow confirmation for long
        "atr_15m": 25.0,
    }
    intents_neg = strat.evaluate_market_event(
        event=event_neg,
        open_positions={},
        current_equity=Decimal("300.0"),
        run_id="TEST_RUN",
        current_replay_ts_ms=event_ts,
    )
    assert len(intents_neg) == 1
    assert intents_neg[0].side == OrderSide.BUY


def test_funding_squeeze_early_and_late_entry_rejection():
    strat = FundingSqueezeStrategy(initial_capital=Decimal("300.0"))
    settle_ts = 1720000000000

    # 1. Early event: T - 2400s (40 minutes before -> > 1800s window)
    early_ts = settle_ts - (2400 * 1000)
    event_early = {
        "symbol": "BTCUSDT",
        "timestamp": early_ts,
        "fundingRate": 0.0008,
        "nextFundingTime": settle_ts,
        "mark_price": 60000.0,
        "vwap": 60050.0,
        "atr_15m": 300.0,
    }
    assert len(strat.evaluate_market_event(event_early, {}, Decimal("300.0"), "TEST", early_ts)) == 0

    # 2. Late event: T - 40s (too close to settlement -> < 60s window)
    late_ts = settle_ts - (40 * 1000)
    event_late = {
        "symbol": "BTCUSDT",
        "timestamp": late_ts,
        "fundingRate": 0.0008,
        "nextFundingTime": settle_ts,
        "mark_price": 60000.0,
        "vwap": 60050.0,
        "atr_15m": 300.0,
    }
    assert len(strat.evaluate_market_event(event_late, {}, Decimal("300.0"), "TEST", late_ts)) == 0


def test_funding_squeeze_stale_and_low_funding_rejection():
    strat = FundingSqueezeStrategy(initial_capital=Decimal("300.0"))
    settle_ts = 1720000000000
    event_ts = settle_ts - (300 * 1000)

    # 1. Low funding rate: 0.01% (< 0.03% threshold)
    event_low_fr = {
        "symbol": "BTCUSDT",
        "timestamp": event_ts,
        "fundingRate": 0.0001,
        "nextFundingTime": settle_ts,
        "mark_price": 60000.0,
        "vwap": 60050.0,
        "atr_15m": 300.0,
    }
    assert len(strat.evaluate_market_event(event_low_fr, {}, Decimal("300.0"), "TEST", event_ts)) == 0

    # 2. Stale event: event_ts is 10,000 ms behind current replay clock
    clock_ts = event_ts + 10000
    event_stale = {
        "symbol": "BTCUSDT",
        "timestamp": event_ts,
        "fundingRate": 0.0005,
        "nextFundingTime": settle_ts,
        "mark_price": 60000.0,
        "vwap": 60050.0,
        "atr_15m": 300.0,
    }
    assert len(strat.evaluate_market_event(event_stale, {}, Decimal("300.0"), "TEST", current_replay_ts_ms=clock_ts)) == 0


def test_funding_squeeze_breakeven_ratchet_and_clearing_eject():
    strat = FundingSqueezeStrategy(initial_capital=Decimal("300.0"))
    settle_ts = 1720000000000
    entry_ts = settle_ts - (600 * 1000)
    entry_p = Decimal("60000.0")
    atr_val = Decimal("300.0")

    pos = ActivePaperPosition(
        symbol="BTCUSDT",
        direction=1,  # LONG
        entry_price=entry_p,
        stop_loss=entry_p - (atr_val * Decimal("1.5")),  # 59,550
        take_profit=entry_p + (atr_val * Decimal("1.2")), # 60,360
        slice_ref=None,
        entry_bar_idx=0,
        entry_ts=entry_ts,
        atr_at_entry=atr_val,
        highest_price=entry_p,
        lowest_price=entry_p,
        fsm_stage=1,
        tp1_done=False,
    )

    # 1. Price moves up +0.6 ATR (+180 to 60,180) -> Dynamic Breakeven Ratchet (+0.15% to 60,090)
    event_gain = {
        "symbol": "BTCUSDT",
        "timestamp": entry_ts + (60 * 1000),
        "mid": 60200.0,
        "nextFundingTime": settle_ts,
    }
    exit_res = strat.evaluate_event_position_exit(pos, event_gain)
    assert exit_res is None  # Still holding
    assert pos.stop_loss == entry_p * Decimal("1.0015")  # Stop ratcheted to 60090.0!

    # 2. Advance time to T - 1.2s before settlement (ms_until_settle = 1200 ms <= 1500 ms)
    eject_ts = settle_ts - 1200
    event_eject = {
        "symbol": "BTCUSDT",
        "timestamp": eject_ts,
        "mid": 60220.0,
        "nextFundingTime": settle_ts,
    }
    exit_eject = strat.evaluate_event_position_exit(pos, event_eject)
    assert exit_eject is not None
    exit_price, exit_reason, exit_side, order_type, _ = exit_eject
    assert exit_reason == "CLEARING_EJECT_T_MINUS_1S"
    assert exit_side == OrderSide.SELL
    assert order_type == OrderType.MARKET
    assert exit_price == Decimal("60220.0")


def test_funding_squeeze_reconnect_preservation_before_settlement():
    """Simulates websocket disconnect right before settlement and reconnect at T - 5s."""
    strat = FundingSqueezeStrategy(initial_capital=Decimal("300.0"))
    settle_ts = 1720000000000
    entry_ts = settle_ts - (900 * 1000)

    pos = ActivePaperPosition(
        symbol="BTCUSDT",
        direction=-1,  # SHORT
        entry_price=Decimal("60000.0"),
        stop_loss=Decimal("60450.0"),
        take_profit=Decimal("59640.0"),
        slice_ref=None,
        entry_bar_idx=0,
        entry_ts=entry_ts,
        atr_at_entry=Decimal("300.0"),
        highest_price=Decimal("60000.0"),
        lowest_price=Decimal("60000.0"),
        fsm_stage=1,
        tp1_done=False,
    )

    # Disconnect occurs: 5 minutes of silence, then reconnect event arrives at T - 5s (settle_ts - 5000)
    reconnect_ts = settle_ts - 5000
    event_reconnect = {
        "symbol": "BTCUSDT",
        "timestamp": reconnect_ts,
        "mid": 59900.0,
        "nextFundingTime": settle_ts,
    }
    # Position state is preserved; at T - 5s still holding
    exit_at_5s = strat.evaluate_event_position_exit(pos, event_reconnect)
    assert exit_at_5s is None

    # Next event at T - 1.0s (settle_ts - 1000) -> Clearing Eject fires immediately!
    event_at_1s = {
        "symbol": "BTCUSDT",
        "timestamp": settle_ts - 1000,
        "mid": 59880.0,
        "nextFundingTime": settle_ts,
    }
    exit_at_1s = strat.evaluate_event_position_exit(pos, event_at_1s)
    assert exit_at_1s is not None
    assert exit_at_1s[1] == "CLEARING_EJECT_T_MINUS_1S"
    assert exit_at_1s[2] == OrderSide.BUY  # Closing short
