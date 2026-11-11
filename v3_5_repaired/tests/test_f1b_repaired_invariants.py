"""
Phase F1B Verification Suite: Tests on v3_5_repaired.
Verifies that all 6 authorized defect fixes (T1, T2, T3, T8, T10, T12)
pass 100% GREEN on AlphaPairsEngineRepaired, while preserving all original signals.
"""

from decimal import Decimal
import time
import pytest
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v3_5_repaired.src.alpha_pairs_engine import (
    AlphaPairsEngineRepaired,
    ActivePairPosition,
    ClosedPairTrade,
)
from v3_5_repaired.src.config import INITIAL_CAPITAL, FEE_TAKER, FEE_MAKER, BASE_SLIPPAGE


class TestF1BRepairedInvariants:
    """
    Verification tests for v3_5_repaired.
    """

    def test_t01_repaired_complete_accounting_invariant(self):
        """
        T1 Repaired: Conservation of Realized Equity upon full closure:
        Delta Realized Equity == Sum(ClosedPairTrade.net_pnl)
        """
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"))
        start_equity = engine.realized_equity

        current_prices = {"BTCUSDT": Decimal("100.0")}
        metrics = {"BTCUSDT": {"atr24": 2.0}}
        bar_ts = int(time.time() * 1000)

        # Enter Long leg (Notional = $30.00 >= $5.00 minNotional)
        entered = engine._enter_leg("BTCUSDT", 1, current_prices, metrics, start_equity, bar_ts)
        assert entered is True
        pos = engine.open_positions["BTCUSDT"]

        # Close position via Stop Loss
        stop_p = pos.stop_price
        current_prices["BTCUSDT"] = stop_p - Decimal("1.0")
        prices_series = {
            s: [{"open": 90.0, "high": 91.0, "low": 89.0, "close": 90.0, "volume": 1000.0}] * 60
            for s in ["BTCUSDT", "ETHUSDT"]
        }

        engine.process_bar(prices_series, current_prices, bar_ts + 3600000)

        assert len(engine.open_positions) == 0
        assert len(engine.closed_trades) == 1

        delta_equity = engine.realized_equity - start_equity
        sum_trade_pnl = sum(t.net_pnl for t in engine.closed_trades)

        # Penny-perfect conservation of capital!
        assert delta_equity == sum_trade_pnl, (
            f"F1B Invariant Failed: Delta Equity={delta_equity} != Sum(Trade Net PnL)={sum_trade_pnl}"
        )

    def test_t02_repaired_partial_tp_slicing_proportional_fee(self):
        """
        T2 Repaired: When Partial TP (+4.0R) triggers, a closed trade slice is
        recorded in closed_trades with proportional entry fee allocated, and
        equity gain matches slice net PnL.
        """
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"))
        start_equity = engine.realized_equity
        current_prices = {"BTCUSDT": Decimal("100.0")}
        metrics = {"BTCUSDT": {"atr24": 2.0}}
        bar_ts = int(time.time() * 1000)

        engine._enter_leg("BTCUSDT", 1, current_prices, metrics, engine.realized_equity, bar_ts)
        pos = engine.open_positions["BTCUSDT"]
        initial_qty = pos.qty
        initial_entry_fee = pos.entry_fee

        # Move price to +4.0R
        target_tp_p = pos.entry_price + (pos.initial_risk_dist * Decimal("4.0"))
        prices_series = {
            s: [{"open": 100.0, "high": 125.0, "low": 99.0, "close": float(target_tp_p), "volume": 1000.0}] * 60
            for s in ["BTCUSDT", "ETHUSDT"]
        }
        current_prices["BTCUSDT"] = target_tp_p

        engine.process_bar(prices_series, current_prices, bar_ts + 3600000)

        # Slice must be recorded in closed_trades!
        assert len(engine.closed_trades) == 1
        slice_trade = engine.closed_trades[0]
        assert slice_trade.reason == "PARTIAL_TP_4R"
        assert slice_trade.qty == initial_qty * Decimal("0.50")
        assert slice_trade.entry_fee_allocated == initial_entry_fee * Decimal("0.50")

        # Position must have 50% qty remaining with 50% entry fee remaining
        assert pos.qty == initial_qty * Decimal("0.50")
        assert pos.entry_fee == initial_entry_fee * Decimal("0.50")

    def test_t03_repaired_stop_gap_slippage_symmetry(self):
        """
        T3 Repaired: Tests Stop-Gap slippage realism for BOTH Long and Short.
        Long gapping down exits <= gap price * (1 - slippage).
        Short gapping up exits >= gap price * (1 + slippage).
        """
        # --- LONG GAP DOWN ---
        engine_long = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"))
        current_prices = {"BTCUSDT": Decimal("100.0")}
        metrics = {"BTCUSDT": {"atr24": 2.0}}
        bar_ts = int(time.time() * 1000)

        engine_long._enter_leg("BTCUSDT", 1, current_prices, metrics, engine_long.realized_equity, bar_ts)
        pos_l = engine_long.open_positions["BTCUSDT"]
        assert pos_l.stop_price == Decimal("95.0")

        # Market gaps to 80.0
        gap_down_p = Decimal("80.0")
        current_prices["BTCUSDT"] = gap_down_p
        prices_series_l = {
            s: [{"open": 80.0, "high": 81.0, "low": 79.0, "close": 80.0, "volume": 1000.0}] * 60
            for s in ["BTCUSDT", "ETHUSDT"]
        }

        engine_long.process_bar(prices_series_l, current_prices, bar_ts + 3600000)
        trade_l = engine_long.closed_trades[0]
        # Must exit at <= 80 * (1 - slippage) = 79.96
        assert trade_l.exit_price <= gap_down_p * (Decimal("1.0") - engine_long.slippage)

        # --- SHORT GAP UP ---
        engine_short = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"))
        current_prices_s = {"ETHUSDT": Decimal("100.0")}
        metrics_s = {"ETHUSDT": {"atr24": 2.0}}

        engine_short._enter_leg("ETHUSDT", -1, current_prices_s, metrics_s, engine_short.realized_equity, bar_ts)
        pos_s = engine_short.open_positions["ETHUSDT"]
        assert pos_s.stop_price == Decimal("105.0")

        # Market gaps up to 120.0
        gap_up_p = Decimal("120.0")
        current_prices_s["ETHUSDT"] = gap_up_p
        prices_series_s = {
            s: [{"open": 120.0, "high": 121.0, "low": 119.0, "close": 120.0, "volume": 1000.0}] * 60
            for s in ["BTCUSDT", "ETHUSDT"]
        }

        engine_short.process_bar(prices_series_s, current_prices_s, bar_ts + 3600000)
        trade_s = engine_short.closed_trades[0]
        # Must exit at >= 120 * (1 + slippage) = 120.06
        assert trade_s.exit_price >= gap_up_p * (Decimal("1.0") + engine_short.slippage)

    def test_t08_repaired_min_notional_rejection(self):
        """
        T8 Repaired: An order with notional < min_notional ($5.0) is rejected.
        """
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("50.0"))
        current_prices = {"BTCUSDT": Decimal("100.0")}
        # Wide ATR -> tiny qty -> notional $0.40 < $5.00
        metrics = {"BTCUSDT": {"atr24": 50.0}}
        bar_ts = int(time.time() * 1000)

        entered = engine._enter_leg("BTCUSDT", 1, current_prices, metrics, engine.realized_equity, bar_ts)
        # Must be rejected!
        assert entered is False
        assert "BTCUSDT" not in engine.open_positions

    def test_t10_repaired_v38_no_synthetic_apt_trade(self):
        """
        T10 Repaired: v3_8 paper_trader_v38.py has no hardcoded synthetic APT trade.
        """
        source_code = Path(REPO_ROOT / "v3_8" / "src" / "paper_trader_v38.py").read_text(encoding="utf-8")
        assert 'symbol="APTUSDT"' not in source_code
        assert "STAGE2_MAKER_TP (+1.40R)" not in source_code

    def test_t12_repaired_bad_data_spike_ignored(self):
        """
        T12 Repaired: An anomalous wick/spike (+100%) is ignored and does NOT drag stop price.
        """
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"))
        current_prices = {"BTCUSDT": Decimal("100.0")}
        metrics = {"BTCUSDT": {"atr24": 2.0}}
        bar_ts = int(time.time() * 1000)

        engine._enter_leg("BTCUSDT", 1, current_prices, metrics, engine.realized_equity, bar_ts)
        pos = engine.open_positions["BTCUSDT"]
        initial_stop = pos.stop_price

        # Corrupt anomalous spike tick: 200.0 (+100%)
        corrupt_spike_p = Decimal("200.0")
        current_prices["BTCUSDT"] = corrupt_spike_p
        prices_series = {
            s: [{"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1000.0}] * 60
            for s in ["BTCUSDT", "ETHUSDT"]
        }

        engine.process_bar(prices_series, current_prices, bar_ts + 3600000)

        # Stop price must remain at initial_stop (95.0), NOT dragged to 196.0!
        assert pos.stop_price == initial_stop
        assert pos.highest_p == Decimal("100.0")  # corrupt tick was discarded!
