"""
Verifies Defect 01: The Artificial OHLC Candle Bug (open = high = low = close)
which made Range Mean-Reversion limit fills mathematically impossible in v3.2 Legacy.
Audit Deliverable: V32-LEGACY-FORENSIC-001.
"""

import sys
from pathlib import Path
from decimal import Decimal
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v3_2.src.range_engine import RangeLimitOrder, RangeMeanReversionEngine


def test_synthetic_candle_buy_limit_impossibility():
    """
    Mathematical Proof:
    Buy limit fill requires:
      next_low <= (order.price - pen_buf) AND next_open >= order.price
    When next_low == next_open == P:
      P <= (order.price - pen_buf) => P < order.price (for pen_buf > 0)
      AND P >= order.price
    This is mutually exclusive for all real numbers P.
    """
    atr = Decimal("1.0")
    pen_buf = atr * Decimal("0.05")  # 0.05
    limit_price = Decimal("100.0")

    order = RangeLimitOrder(
        symbol="BTCUSDT",
        direction=1,
        price=limit_price,
        qty=Decimal("1.0"),
        stop_loss=Decimal("95.0"),
        take_profit=Decimal("105.0"),
        created_time=1700000000000,
        created_bar_idx=0,
        atr=atr,
    )

    # Test across a sweep of 2,000 potential market prices
    for p_int in range(9000, 11000):
        p = Decimal(p_int) / Decimal("100.0")  # 90.00 to 110.00
        next_low = p
        next_open = p

        # Condition from RangeMeanReversionEngine.evaluate_pending_fills
        filled = (next_low <= (order.price - pen_buf)) and (next_open >= order.price)
        assert filled is False, f"Impossible fill occurred at mark price {p}!"


def test_synthetic_candle_sell_limit_impossibility():
    """
    Mathematical Proof:
    Sell limit fill requires:
      next_high >= (order.price + pen_buf) AND next_open <= order.price
    When next_high == next_open == P:
      P >= (order.price + pen_buf) => P > order.price (for pen_buf > 0)
      AND P <= order.price
    This is mutually exclusive for all real numbers P.
    """
    atr = Decimal("1.0")
    pen_buf = atr * Decimal("0.05")
    limit_price = Decimal("100.0")

    order = RangeLimitOrder(
        symbol="BTCUSDT",
        direction=-1,
        price=limit_price,
        qty=Decimal("1.0"),
        stop_loss=Decimal("105.0"),
        take_profit=Decimal("95.0"),
        created_time=1700000000000,
        created_bar_idx=0,
        atr=atr,
    )

    for p_int in range(9000, 11000):
        p = Decimal(p_int) / Decimal("100.0")
        next_high = p
        next_open = p

        filled = (next_high >= (order.price + pen_buf)) and (next_open <= order.price)
        assert filled is False, f"Impossible fill occurred at mark price {p}!"


def test_range_engine_mock_bars_zero_fills():
    """
    Simulates RangeMeanReversionEngine with synthetic next_bars exactly as constructed in
    v3_2/src/paper_trader_v32.py (lines 547-554).
    """
    engine = RangeMeanReversionEngine(initial_capital=Decimal("300.0"))

    # Place a pending buy limit order
    order = RangeLimitOrder(
        symbol="ETHUSDT",
        direction=1,
        price=Decimal("2000.0"),
        qty=Decimal("0.1"),
        stop_loss=Decimal("1900.0"),
        take_profit=Decimal("2100.0"),
        created_time=1700000000000,
        created_bar_idx=0,
        atr=Decimal("20.0"),
    )
    engine.pending_orders["ETHUSDT"] = order

    # Construct synthetic candle exactly as paper_trader_v32.py does
    mark_prices = [1950.0, 1990.0, 2000.0, 2010.0, 2050.0]
    for mark in mark_prices:
        next_bars_mock = {
            "ETHUSDT": {
                "open": float(mark),
                "high": float(mark),
                "low": float(mark),
                "close": float(mark),
            }
        }
        next_open_prices = {"ETHUSDT": Decimal(str(mark))}

        filled_symbols = engine.evaluate_pending_fills(next_bars_mock, next_open_prices)
        assert len(filled_symbols) == 0
        assert "ETHUSDT" in engine.pending_orders  # order never fills!


def test_range_engine_real_candle_successful_fill():
    """
    Confirms that under realistic OHLC bars (where Low penetrates below limit while Open was above),
    the Range engine DOES successfully fill orders.
    """
    engine = RangeMeanReversionEngine(initial_capital=Decimal("300.0"))

    order = RangeLimitOrder(
        symbol="ETHUSDT",
        direction=1,
        price=Decimal("2000.0"),
        qty=Decimal("0.1"),
        stop_loss=Decimal("1900.0"),
        take_profit=Decimal("2100.0"),
        created_time=1700000000000,
        created_bar_idx=0,
        atr=Decimal("20.0"),
    )
    engine.pending_orders["ETHUSDT"] = order

    # Real bar: Opened at 2005.0 (above limit), dipped to 1995.0 (penetrates 2000 - 1.0)
    real_bar = {
        "ETHUSDT": {
            "open": 2005.0,
            "high": 2010.0,
            "low": 1995.0,
            "close": 2002.0,
        }
    }
    next_open_prices = {"ETHUSDT": Decimal("2005.0")}

    filled_symbols = engine.evaluate_pending_fills(real_bar, next_open_prices)
    assert len(filled_symbols) == 1
    assert filled_symbols[0] == "ETHUSDT"
    assert "ETHUSDT" not in engine.pending_orders
    assert "ETHUSDT" in engine.open_positions
