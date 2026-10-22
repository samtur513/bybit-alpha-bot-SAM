"""
Unit tests for Auto-Gearbox risk controller and Fast-Scratch breakout defense (v3.1).
Strict adherence to TDD, Zero-Lookahead PIT, and Deep Module Design.
"""

from decimal import Decimal
import pytest
from v3.src.kelly_sizing import KellyRiskManager
from v3.src.core_v3 import CoreV3Engine


class TestAutoGearbox:
    """Tests for Auto-Gearbox multi-tier risk stepping and recovery."""

    def test_initial_state_gear3(self):
        """Initial state should start in Gear 3 (High Momentum, 2.0% risk, pyramiding allowed)."""
        km = KellyRiskManager(base_risk_pct=Decimal("0.02"))
        assert km.current_gear == 3
        assert km.is_pyramiding_allowed is True
        assert km.get_effective_base_risk_pct() == Decimal("0.02")

    def test_downshift_to_gear2_on_10pct_drawdown(self):
        """Drawdown >= 10.0% downshifts Gear 3 -> Gear 2 (1.0% risk, pyramiding blocked)."""
        km = KellyRiskManager(base_risk_pct=Decimal("0.02"))
        gear = km.update_drawdown(equity=Decimal("900.0"), peak_equity=Decimal("1000.0"))
        assert gear == 2
        assert km.current_gear == 2
        assert km.is_pyramiding_allowed is False
        assert km.get_effective_base_risk_pct() == Decimal("0.01")

    def test_downshift_to_gear1_on_16pct_drawdown(self):
        """Drawdown >= 16.0% downshifts Gear 2 -> Gear 1 (0.5% risk, capital preservation)."""
        km = KellyRiskManager(base_risk_pct=Decimal("0.02"))
        km.update_drawdown(equity=Decimal("900.0"), peak_equity=Decimal("1000.0"))
        gear = km.update_drawdown(equity=Decimal("830.0"), peak_equity=Decimal("1000.0"))
        assert gear == 1
        assert km.current_gear == 1
        assert km.is_pyramiding_allowed is False
        assert km.get_effective_base_risk_pct() == Decimal("0.005")

    def test_upshift_on_50pct_recovery_from_trough(self):
        """Recovering 50% of drawdown depth from the trough upshifts the gearbox."""
        km = KellyRiskManager(base_risk_pct=Decimal("0.02"))
        # Drop to 800 (20% DD trough, trough loss = 200)
        km.update_drawdown(equity=Decimal("800.0"), peak_equity=Decimal("1000.0"))
        assert km.current_gear == 1

        # Partial bounce to 850 (25% recovery, remains in Gear 1)
        km.update_drawdown(equity=Decimal("850.0"), peak_equity=Decimal("1000.0"))
        assert km.current_gear == 1

        # Bounce to 910 (55% recovery from 800 trough -> upshifts to Gear 2)
        gear = km.update_drawdown(equity=Decimal("910.0"), peak_equity=Decimal("1000.0"))
        assert gear == 2
        assert km.current_gear == 2

        # Full recovery to 990 (DD 1.0% < 5.0% threshold -> upshifts to Gear 3)
        gear = km.update_drawdown(equity=Decimal("990.0"), peak_equity=Decimal("1000.0"))
        assert gear == 3
        assert km.is_pyramiding_allowed is True
        assert km.get_effective_base_risk_pct() == Decimal("0.02")


class TestFastScratchExit:
    """Tests for Fast-Scratch Exit when breakout stalls inside 3 bars."""

    def test_fast_scratch_triggered_on_false_breakout_within_3_bars(self):
        """
        If a long breakout retreats back below entry within 3 bars with negative or negligible profit,
        the position is closed early with FAST_SCRATCH instead of waiting for a full -1R stop.
        """
        rules = {
            "BTCUSDT": {
                "minOrderQty": Decimal("0.001"),
                "qtyStep": Decimal("0.001"),
                "minNotional": Decimal("5.0"),
            }
        }
        engine = CoreV3Engine(
            initial_capital=Decimal("1000.0"),
            instrument_rules=rules,
            base_risk_pct=Decimal("0.02"),
            cooldown_bars=3,
        )

        t0 = 1700000000000
        bars_t0 = {
            "BTCUSDT": {
                "open": 50000.0,
                "high": 51000.0,
                "low": 49800.0,
                "close": 50900.0,
                "upper": 50500.0,  # breakout triggered!
                "lower": 48000.0,
                "ema_fast": 50000.0,
                "ema_slow": 49000.0,
                "atr": 1000.0,
                "volume": 1000.0,
                "volume_sma": 500.0,
                "adx": 25.0,
            }
        }
        # Bar 0 closed -> triggers entry at next open 50900
        next_open = {"BTCUSDT": Decimal("50900.0")}
        engine.process_bar_event(t0, bars_t0, next_open)
        assert "BTCUSDT" in engine.open_positions

        pos = engine.open_positions["BTCUSDT"]
        entry_price = pos.entry_price

        # Bar 1: Price stalls and dips back below entry_price (e.g. 50400), but stays above stop (e.g. stop is ~48400)
        t1 = t0 + 14400000
        bars_t1 = {
            "BTCUSDT": {
                "open": 50900.0,
                "high": 50950.0,
                "low": 50300.0,
                "close": 50400.0,  # dipped below entry 50900 and channel 50500!
                "atr": 1000.0,
            }
        }
        next_open_t2 = {"BTCUSDT": Decimal("50400.0")}
        engine.process_bar_event(t1, bars_t1, next_open_t2)

        # Position should be closed via FAST_SCRATCH to avoid taking full -1R stop
        assert "BTCUSDT" not in engine.open_positions
        assert len(engine.closed_trades) == 1
        closed = engine.closed_trades[0]
        assert closed.exit_reason == "FAST_SCRATCH"
        # Loss should be small scratch (~ -500 price diff), NOT full stop (-2500 price diff)
        assert closed.exit_price == Decimal("50400.0") * (Decimal("1") - engine.slippage)
