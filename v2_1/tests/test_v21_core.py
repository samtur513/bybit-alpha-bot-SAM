"""
Unit Tests for Multi-Alpha Growth Engine v2.1.
Verifies:
1. Bybit MNT fee (0.09% / 0.0009)
2. Weighted average entry price upon pyramiding (+50% add-on)
3. Automated Drawdown Halt (18% threshold)
4. BTC Delta Filter (blocks counter-trend altcoin signals)
5. Re-entry Cooldown (3-bar cooldown on closed symbol/direction)
6. Transactional State Persistence
"""

from decimal import Decimal
import numpy as np
import pytest

from v2_1.src.config import BASE_TAKER_FEE, REENTRY_COOLDOWN_BARS, ENABLE_BTC_DELTA_FILTER
from v2_1.src.multi_scanner import MultiCoinScanner, CoinCandidate
from v2_1.src.pyramiding_engine import PyramidingEngine, ActivePosition
from v2_1.src.core_v2 import CoreV2Engine, ClosedTrade


def test_v21_mnt_fee_configured():
    """Verify that MNT discounted fee is exactly 0.09% (0.0009)."""
    assert BASE_TAKER_FEE == Decimal("0.0009")


def test_v21_pyramiding_weighted_average_entry():
    """
    Verify P0 fix: Adding to a winning position recalculates the weighted
    average entry price, eliminating phantom pyramiding profits.
    """
    engine = CoreV2Engine(
        initial_capital=Decimal("1000.0"),
        taker_fee=Decimal("0.0009"),
        slippage=Decimal("0.0"),  # zero slippage for exact math check
        instrument_rules={
            "ETHUSDT": {
                "minOrderQty": Decimal("0.01"),
                "qtyStep": Decimal("0.01"),
                "minNotional": Decimal("5.0"),
                "maxOrderQty": Decimal("1000"),
            }
        },
    )

    # Initial position: 1.0 ETH entered at $100.00, ATR = 4.0
    engine.open_positions["ETHUSDT"] = ActivePosition(
        symbol="ETHUSDT",
        direction=1,
        initial_qty=Decimal("1.0"),
        current_qty=Decimal("1.0"),
        entry_price=Decimal("100.0"),
        current_stop=Decimal("90.0"),
        initial_atr=Decimal("4.0"),
        highest_price=Decimal("100.0"),
        lowest_price=Decimal("100.0"),
        entry_time=1000,
        original_entry=Decimal("100.0"),
    )

    # Price moves to $108.00 (+2 ATR = +8). Pyramiding triggers add of +50% (0.5 ETH) at 108.
    prices = {"ETHUSDT": Decimal("108.0")}
    engine._manage(
        sym="ETHUSDT",
        timestamp=2000,
        high=Decimal("108.0"),
        low=Decimal("104.0"),
        close=Decimal("108.0"),
        atr=Decimal("4.0"),
        execution=Decimal("108.0"),
        prices=prices,
    )

    pos = engine.open_positions["ETHUSDT"]
    assert pos.is_pyramided is True
    assert pos.current_qty == Decimal("1.5")
    # Weighted average: (1.0 * 100 + 0.5 * 108) / 1.5 = 154 / 1.5 = 102.6666...
    expected_avg = (Decimal("1.0") * Decimal("100.0") + Decimal("0.5") * Decimal("108.0")) / Decimal("1.5")
    assert abs(pos.entry_price - expected_avg) < Decimal("0.0001")


def test_v21_drawdown_limit_halts_trading():
    """Verify that reaching 18% drawdown triggers trading_halted."""
    engine = CoreV2Engine(
        initial_capital=Decimal("300.0"),
        drawdown_limit_pct=Decimal("18.0"),
    )
    # Peak starts at 300
    assert engine.trading_halted is False

    # Simulate price drop causing equity drop from 300 to 240 (-20% drawdown)
    engine.open_positions["BTCUSDT"] = ActivePosition(
        symbol="BTCUSDT",
        direction=1,
        initial_qty=Decimal("0.01"),
        current_qty=Decimal("0.01"),
        entry_price=Decimal("30000.0"),
        current_stop=Decimal("20000.0"),
        initial_atr=Decimal("1000.0"),
        highest_price=Decimal("30000.0"),
        lowest_price=Decimal("24000.0"),
        entry_time=1000,
    )
    # Mark price drops to 24000 -> unrealized loss = 0.01 * (24000 - 30000) = -60
    # Equity = 300 - 60 = 240 -> Drawdown = 60/300 = 20% >= 18%
    engine.process_price_event(2000, {"BTCUSDT": Decimal("24000.0")})

    assert engine.trading_halted is True


def test_v21_btc_delta_filter_blocks_counter_trend_alts():
    """
    BTC Delta Filter:
    - If btc_bullish is False: Altcoin Long candidates must be filtered out.
    - BTCUSDT itself must NEVER be filtered out by its own filter.
    - If btc_bullish is True: Altcoin Long candidates are accepted.
    """
    scanner = MultiCoinScanner(
        max_concurrent_positions=2,
        enable_btc_delta_filter=True,
    )

    btc_cand = CoinCandidate(
        symbol="BTCUSDT",
        direction=1,
        close_price=Decimal("65000"),
        breakout_boundary=Decimal("64000"),
        atr=Decimal("1000"),
        trend_momentum_score=2.5,
        volume_ratio=1.5,
        normalized_breakout_dist=1.0,
    )
    sol_cand = CoinCandidate(
        symbol="SOLUSDT",
        direction=1,
        close_price=Decimal("150"),
        breakout_boundary=Decimal("145"),
        atr=Decimal("5.0"),
        trend_momentum_score=3.0,
        volume_ratio=1.8,
        normalized_breakout_dist=1.0,
    )

    # Scenario A: BTC is bearish (btc_bullish = False)
    # BTCUSDT should pass, but SOLUSDT should be blocked
    selected = scanner.rank_candidates(
        candidates=[sol_cand, btc_cand],
        currently_held_symbols=[],
        current_long_count=0,
        current_short_count=0,
        btc_bullish=False,
        btc_bearish=True,
    )
    assert len(selected) == 1
    assert selected[0].symbol == "BTCUSDT"

    # Scenario B: BTC is bullish (btc_bullish = True)
    # Both should pass, SOLUSDT ranked higher due to TMS (3.0 > 2.5)
    selected_bullish = scanner.rank_candidates(
        candidates=[sol_cand, btc_cand],
        currently_held_symbols=[],
        current_long_count=0,
        current_short_count=0,
        btc_bullish=True,
        btc_bearish=False,
    )
    assert len(selected_bullish) == 2
    assert selected_bullish[0].symbol == "SOLUSDT"
    assert selected_bullish[1].symbol == "BTCUSDT"


def test_v21_reentry_cooldown():
    """
    Re-entry Cooldown:
    - Once a position closes, re-entry on the same symbol and direction is blocked for 3 bars.
    """
    engine = CoreV2Engine(
        initial_capital=Decimal("300.0"),
        cooldown_bars=3,
        taker_fee=Decimal("0.0009"),
        instrument_rules={
            "ETHUSDT": {
                "minOrderQty": Decimal("0.01"),
                "qtyStep": Decimal("0.01"),
                "minNotional": Decimal("5.0"),
            }
        },
    )

    # Open a long position in ETHUSDT manually
    engine.open_positions["ETHUSDT"] = ActivePosition(
        symbol="ETHUSDT",
        direction=1,
        initial_qty=Decimal("0.1"),
        current_qty=Decimal("0.1"),
        entry_price=Decimal("2500.0"),
        current_stop=Decimal("2450.0"),
        initial_atr=Decimal("20.0"),
        highest_price=Decimal("2500.0"),
        lowest_price=Decimal("2450.0"),
        entry_time=1700000000000,
        original_entry=Decimal("2500.0"),
    )

    # Bar 1: Price drops to 2440 hitting stop loss -> Position closes
    bar_1 = {
        "ETHUSDT": {
            "open": 2480.0,
            "high": 2490.0,
            "low": 2440.0,
            "close": 2445.0,
            "atr": 20.0,
            "upper": 2550.0,
            "lower": 2400.0,
            "ema_fast": 2480.0,
            "ema_slow": 2490.0,
            "volume": 1000.0,
            "volume_sma": 800.0,
        }
    }
    engine.process_bar_event(
        timestamp=1700003600000,
        bars=bar_1,
        next_open_prices={"ETHUSDT": Decimal("2445.0")},
        btc_bullish=True,
    )

    assert "ETHUSDT" not in engine.open_positions
    assert len(engine.closed_trades) == 1
    # Bar counter was incremented to 1, cooldown until 1 + 3 = 4
    assert engine.cooldown_tracker.get("ETHUSDT_1") == 4

    # Bar 2: Immediate breakout signal appears on ETHUSDT Long
    # Should be rejected because current bar is 2 < 4
    bar_2 = {
        "ETHUSDT": {
            "open": 2500.0,
            "high": 2560.0,
            "low": 2450.0,
            "close": 2555.0,
            "atr": 20.0,
            "upper": 2550.0,
            "lower": 2400.0,
            "ema_fast": 2520.0,
            "ema_slow": 2500.0,
            "volume": 2000.0,
            "volume_sma": 1000.0,
        }
    }
    engine.process_bar_event(
        timestamp=1700007200000,
        bars=bar_2,
        next_open_prices={"ETHUSDT": Decimal("2555.0")},
        btc_bullish=True,
    )
    # Re-entry must be BLOCKED
    assert "ETHUSDT" not in engine.open_positions

    # Bar 3: Another bar passes
    engine.process_bar_event(
        timestamp=1700010800000,
        bars=bar_2,
        next_open_prices={"ETHUSDT": Decimal("2555.0")},
        btc_bullish=True,
    )
    assert "ETHUSDT" not in engine.open_positions

    # Bar 4: Cooldown expires (current bar 4 == 4)
    engine.process_bar_event(
        timestamp=1700014400000,
        bars=bar_2,
        next_open_prices={"ETHUSDT": Decimal("2555.0")},
        btc_bullish=True,
    )
    # Now allowed to re-enter!
    assert "ETHUSDT" in engine.open_positions


def test_v21_persistence_roundtrip():
    """Verify that serialization preserves v2.1 cooldowns and state."""
    engine = CoreV2Engine(initial_capital=Decimal("300.0"))
    engine.realized_equity = Decimal("345.50")
    engine.bar_counter = 42
    engine.cooldown_tracker = {"SOLUSDT_1": 45, "BTCUSDT_-1": 43}

    data = engine.to_dict()
    assert data["version"] == "2.1"
    assert data["realized_equity"] == "345.50"
    assert data["bar_counter"] == 42
    assert data["cooldown_tracker"] == {"SOLUSDT_1": 45, "BTCUSDT_-1": 43}

    # Restore in new engine
    new_engine = CoreV2Engine(initial_capital=Decimal("300.0"))
    new_engine.load_from_dict(data)

    assert new_engine.realized_equity == Decimal("345.50")
    assert new_engine.bar_counter == 42
    assert new_engine.cooldown_tracker == {"SOLUSDT_1": 45, "BTCUSDT_-1": 43}
