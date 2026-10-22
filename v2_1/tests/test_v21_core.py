"""
Unit Tests for Multi-Alpha Growth Engine v2.1.
Verifies:
1. Bybit MNT fee (0.09% / 0.0009)
2. BTC Delta Filter (blocks counter-trend altcoin signals)
3. Re-entry Cooldown (3-bar cooldown on closed symbol/direction)
4. State Serialization / Deserialization (atomic persistence)
"""

from decimal import Decimal
import pytest

from v2_1.src.config import BASE_TAKER_FEE, REENTRY_COOLDOWN_BARS, ENABLE_BTC_DELTA_FILTER
from v2_1.src.multi_scanner import MultiCoinScanner, CoinCandidate
from v2_1.src.pyramiding_engine import PyramidingEngine, ActivePosition
from v2_1.src.core_v2 import CoreV2Engine, ClosedTrade


def test_v21_mnt_fee_configured():
    """Verify that MNT discounted fee is exactly 0.09% (0.0009)."""
    assert BASE_TAKER_FEE == Decimal("0.0009")


def test_v21_btc_delta_filter_blocks_counter_trend_alts():
    """
    BTC Delta Filter:
    - If btc_bullish is False: Altcoin Long candidates must be filtered out.
    - BTCUSDT itself must NEVER be filtered out by its own filter.
    - If btc_bullish is True: Altcoin Long candidates are accepted.
    """
    scanner = MultiCoinScanner(
        max_long_positions=2,
        max_short_positions=2,
        enable_btc_delta_filter=True,
    )

    btc_cand = CoinCandidate(
        symbol="BTCUSDT",
        direction=1,
        tms_score=Decimal("2.5"),
        atr=Decimal("1000"),
        breakout_price=Decimal("65000"),
    )
    sol_cand = CoinCandidate(
        symbol="SOLUSDT",
        direction=1,
        tms_score=Decimal("3.0"),
        atr=Decimal("5.0"),
        breakout_price=Decimal("150"),
    )

    # Scenario A: BTC is bearish (btc_bullish = False)
    # BTCUSDT should pass, but SOLUSDT should be blocked
    selected = scanner.rank_candidates(
        long_candidates=[sol_cand, btc_cand],
        short_candidates=[],
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
        long_candidates=[sol_cand, btc_cand],
        short_candidates=[],
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
        is_pyramided=False,
        is_breakeven_set=False,
    )

    # Bar 1: Price drops to 2440 hitting stop loss -> Position closes
    bar_1 = {
        "ETHUSDT": {
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
        bar_timestamp=1700003600000,
        bars_dict=bar_1,
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
        bar_timestamp=1700007200000,
        bars_dict=bar_2,
        next_open_prices={"ETHUSDT": Decimal("2555.0")},
        btc_bullish=True,
    )
    # Re-entry must be BLOCKED
    assert "ETHUSDT" not in engine.open_positions

    # Bar 3: Another bar passes
    engine.process_bar_event(
        bar_timestamp=1700010800000,
        bars_dict=bar_2,
        next_open_prices={"ETHUSDT": Decimal("2555.0")},
        btc_bullish=True,
    )
    assert "ETHUSDT" not in engine.open_positions

    # Bar 4: Cooldown expires (current bar 4 == 4)
    engine.process_bar_event(
        bar_timestamp=1700014400000,
        bars_dict=bar_2,
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
