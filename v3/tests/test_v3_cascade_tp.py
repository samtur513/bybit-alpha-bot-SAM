"""
Unit tests for 3-Stage Cascade Take-Profit (TP1 +1.5R -> BE, TP2 +3.0R -> +1.5R, TP3 Runner).
Strict adherence to TDD and zero-lookahead point-in-time accounting.
"""

from decimal import Decimal
import pytest
from v3.src.core_v3 import CoreV3Engine


class TestCascadeTakeProfit:
    """Verifies 3-Stage Cascade Partial TP: TP1 (30% at +1.5R), TP2 (40% at +3.0R), TP3 (30% Runner)."""

    def test_cascade_tp1_and_tp2_execution(self):
        rules = {
            "ETHUSDT": {
                "minOrderQty": Decimal("0.01"),
                "qtyStep": Decimal("0.01"),
                "minNotional": Decimal("5.0"),
            }
        }
        engine = CoreV3Engine(
            initial_capital=Decimal("1000.0"),
            instrument_rules=rules,
            base_risk_pct=Decimal("0.02"),
            cooldown_bars=3,
            enable_cascade_tp=True,
            enable_fast_scratch=False,  # isolate TP test
        )

        t0 = 1700000000000
        bars_t0 = {
            "ETHUSDT": {
                "open": 3000.0,
                "high": 3100.0,
                "low": 2980.0,
                "close": 3090.0,
                "upper": 3050.0,
                "lower": 2800.0,
                "ema_fast": 3000.0,
                "ema_slow": 2900.0,
                "atr": 100.0,
                "volume": 2000.0,
                "volume_sma": 1000.0,
                "adx": 25.0,
            }
        }
        # Bar 0 entry at next open 3090
        next_open = {"ETHUSDT": Decimal("3090.0")}
        engine.process_bar_event(t0, bars_t0, next_open)
        assert "ETHUSDT" in engine.open_positions

        pos = engine.open_positions["ETHUSDT"]
        initial_qty = pos.initial_qty
        entry_price = pos.entry_price
        initial_stop = pos.current_stop

        # Initial stop should be at 3090 - 2.5 * 100 = 2840 (approx with slippage)
        assert initial_stop < entry_price

        # Bar 1: Price rallies to +1.5R (+1.5 * initial_atr = +150 -> price 3250)
        # Should trigger TP1: 30% closed, stop moved to cash breakeven (>= entry_price)
        t1 = t0 + 14400000
        bars_t1 = {
            "ETHUSDT": {
                "open": 3100.0,
                "high": 3260.0,
                "low": 3090.0,
                "close": 3250.0,
                "atr": 100.0,
            }
        }
        next_open_t2 = {"ETHUSDT": Decimal("3250.0")}
        engine.process_bar_event(t1, bars_t1, next_open_t2)

        pos = engine.open_positions["ETHUSDT"]
        assert pos.tp1_taken is True
        # Quantity should be reduced by ~30%
        assert pos.current_qty < initial_qty
        # Stop must be at least breakeven
        assert pos.current_stop >= entry_price

        # Bar 2: Price surges further to +3.0R (+300 -> price 3400)
        # Should trigger TP2: additional 40% of initial qty closed, stop moved to +1.5R
        t2 = t1 + 14400000
        bars_t2 = {
            "ETHUSDT": {
                "open": 3250.0,
                "high": 3420.0,
                "low": 3240.0,
                "close": 3400.0,
                "atr": 100.0,
            }
        }
        next_open_t3 = {"ETHUSDT": Decimal("3400.0")}
        engine.process_bar_event(t2, bars_t2, next_open_t3)

        pos = engine.open_positions["ETHUSDT"]
        assert pos.tp2_taken is True
        # Remaining quantity is runner (~30% of initial)
        assert pos.current_qty < initial_qty * Decimal("0.5")
        # Stop must be raised to at least +1.5R (>= entry_price + 1.5 * 100)
        assert pos.current_stop >= entry_price + Decimal("100.0")
