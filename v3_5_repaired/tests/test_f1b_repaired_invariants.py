"""
Phase F1B Verification Suite: Tests on v3_5_repaired.
Verifies that all institutional defect fixes and quantitative invariants
pass 100% GREEN on AlphaPairsEngineRepaired, PaperTraderV35Repaired, and FinancialLedger,
while strictly preserving all original 72h momentum and pair-trading rules.
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
    BybitInstrumentRule,
)
from v3_5_repaired.src.paper_trader_v35 import PaperTraderV35Repaired
from v3_5_repaired.src.financial_ledger import FinancialLedger
from v3_5_repaired.src.config import (
    INITIAL_CAPITAL,
    FEE_TAKER,
    FEE_MAKER,
    BASE_SLIPPAGE,
    MIN_NOTIONAL_USDT,
)


class TestF1BRepairedInvariants:
    """
    Complete Verification Test Suite for Phase F1B.
    """

    # -------------------------------------------------------------------------
    # T1: Financial Ledger: Complete Entry + Exit Fee Accounting
    # -------------------------------------------------------------------------
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
        entered = engine._enter_leg(
            "BTCUSDT", 1, current_prices, metrics, start_equity, bar_ts,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=bar_ts, current_clock=bar_ts,
        )
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
        is_balanced, _, _ = engine.verify_ledger_conservation()
        assert is_balanced is True

    # -------------------------------------------------------------------------
    # T2: Partial Take Profit Slicing with Proportional Fee Allocation
    # -------------------------------------------------------------------------
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

        engine._enter_leg(
            "BTCUSDT", 1, current_prices, metrics, engine.realized_equity, bar_ts,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=bar_ts, current_clock=bar_ts,
        )
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

    # -------------------------------------------------------------------------
    # T3: Stop Gap Slippage Realism with Long/Short Symmetry
    # -------------------------------------------------------------------------
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

        engine_long._enter_leg(
            "BTCUSDT", 1, current_prices, metrics, engine_long.realized_equity, bar_ts,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=bar_ts, current_clock=bar_ts,
        )
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

        engine_short._enter_leg(
            "ETHUSDT", -1, current_prices_s, metrics_s, engine_short.realized_equity, bar_ts,
            bid=Decimal("100.00"), ask=Decimal("100.02"), quote_ts=bar_ts, current_clock=bar_ts,
        )
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

    # -------------------------------------------------------------------------
    # T4: Kill Switch: Blocks Entries while Protecting Open Positions
    # -------------------------------------------------------------------------
    def test_t04_repaired_kill_switch_blocks_entries_protects_positions(self):
        """
        T4 Repaired: When trading_halted = True:
        1. New entries are strictly prohibited even with valid candidates.
        2. Existing open positions continue to be monitored and stopped out.
        """
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"))
        bar_ts = int(time.time() * 1000)

        # First, enter an initial position
        current_prices = {"BTCUSDT": Decimal("100.0"), "ETHUSDT": Decimal("100.0"), "SOLUSDT": Decimal("100.0")}
        metrics = {"BTCUSDT": {"atr24": 2.0}, "ETHUSDT": {"atr24": 2.0}, "SOLUSDT": {"atr24": 2.0}}
        engine._enter_leg(
            "BTCUSDT", 1, current_prices, metrics, engine.realized_equity, bar_ts,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=bar_ts, current_clock=bar_ts,
        )
        assert len(engine.open_positions) == 1

        # Activate Kill Switch (halt trading)
        engine.trading_halted = True

        # Candidate series where ETH/SOL would normally trigger new entry
        btc_bars = [{"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 1000.0}] * 75
        eth_bars = [{"open": 100.0, "high": 105.0, "low": 99.0, "close": 100.0 + i * 0.4, "volume": 1000.0} for i in range(75)]
        sol_bars = [{"open": 100.0, "high": 101.0, "low": 69.0, "close": 100.0 - i * 0.4, "volume": 1000.0} for i in range(75)]

        prices_series = {"BTCUSDT": btc_bars, "ETHUSDT": eth_bars, "SOLUSDT": sol_bars}
        # BTC price drops to 90 (breaches stop of 95)
        current_prices["BTCUSDT"] = Decimal("90.0")

        engine.process_bar(prices_series, current_prices, bar_ts + 3600000)

        # Verification 1: Existing position WAS safely stopped out!
        assert len(engine.closed_trades) == 1
        assert engine.closed_trades[0].reason == "STOP_LOSS"

        # Verification 2: NO new positions were entered!
        assert len(engine.open_positions) == 0

    # -------------------------------------------------------------------------
    # T5: Event-Driven Kline Pipeline: confirm=True Enforcement
    # -------------------------------------------------------------------------
    def test_t05_repaired_event_driven_kline_confirm_true(self):
        """
        T5 Repaired: PaperTraderV35Repaired.on_kline_event rejects unconfirmed bars
        and accepts confirmed bars.
        """
        trader = PaperTraderV35Repaired.__new__(PaperTraderV35Repaired)
        import threading
        trader.lock = threading.RLock()
        trader.symbols = ["BTCUSDT", "ETHUSDT"]
        trader.kline_history = {"BTCUSDT": [], "ETHUSDT": []}
        trader.latest_prices = {}
        trader.processed_kline_keys = set()
        trader.pending_bars_by_ts = {}
        trader.pending_bars_first_seen = {}
        trader.last_processed_bar_ts = 0

        unconfirmed_msg = {
            "topic": "kline.60.BTCUSDT",
            "data": [{"start": 1794000000000, "close": "73000.0", "confirm": False, "open": "72900", "high": "73100", "low": "72800", "volume": "50"}],
        }
        confirmed_msg = {
            "topic": "kline.60.BTCUSDT",
            "data": [{"start": 1794000000000, "close": "73100.0", "confirm": True, "open": "72900", "high": "73150", "low": "72800", "volume": "100"}],
        }

        # Unconfirmed bar must be rejected
        res_unconf = trader.on_kline_event(unconfirmed_msg)
        assert res_unconf is False
        assert len(trader.kline_history["BTCUSDT"]) == 0

        # Confirmed bar must be accepted
        res_conf = trader.on_kline_event(confirmed_msg)
        assert res_conf is True
        assert trader.latest_prices["BTCUSDT"] == Decimal("73100.0")

    # -------------------------------------------------------------------------
    # T6: Signed Absolute Portfolio Beta Risk Monitoring
    # -------------------------------------------------------------------------
    def test_t06_repaired_signed_statistical_portfolio_beta(self):
        """
        T6 Repaired: calculate_portfolio_beta calculates true covariance beta
        relative to BTC benchmark using time-synchronized bars.
        """
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"))
        # Synthetic 50-bar price series with start timestamps
        btc_bars = [{"start": i * 3600000, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0 + i * 0.2, "volume": 1000.0} for i in range(50)]
        # ETH has 1.5x beta (moves 1.5x of BTC)
        eth_bars = [{"start": i * 3600000, "open": 100.0, "high": 101.5, "low": 98.5, "close": 100.0 + i * 0.3, "volume": 1000.0} for i in range(50)]
        # SOL has 0.8x beta
        sol_bars = [{"start": i * 3600000, "open": 100.0, "high": 100.8, "low": 99.2, "close": 100.0 + i * 0.16, "volume": 1000.0} for i in range(50)]

        prices_series = {"BTCUSDT": btc_bars, "ETHUSDT": eth_bars, "SOLUSDT": sol_bars}

        # Put 1 Long on ETH and 1 Short on SOL
        engine.open_positions["ETHUSDT"] = ActivePairPosition("ETHUSDT", 1, Decimal("1.0"), Decimal("100.0"), Decimal("95.0"), Decimal("5.0"), Decimal("2.0"), Decimal("100.0"), Decimal("100.0"), 0, 0)
        engine.open_positions["SOLUSDT"] = ActivePairPosition("SOLUSDT", -1, Decimal("1.0"), Decimal("100.0"), Decimal("105.0"), Decimal("5.0"), Decimal("2.0"), Decimal("100.0"), Decimal("100.0"), 0, 0)

        port_beta_res = engine.calculate_portfolio_beta(prices_series)
        assert port_beta_res.status.value == "VALID"
        assert port_beta_res.beta is not None
        assert isinstance(port_beta_res.beta, Decimal)
        assert port_beta_res.beta > Decimal("0.10"), f"Expected positive spread beta, got {port_beta_res.beta}"

    # -------------------------------------------------------------------------
    # T8: Bybit Exchange Constraints (Lot Sizing, Step & MinNotional)
    # -------------------------------------------------------------------------
    def test_t08_repaired_full_bybit_contract_specifications(self):
        """
        T8 Repaired: Verifies full Bybit contract rules:
        - minNotionalValue rejection
        - qtyStep flooring (e.g. SOL 0.1 step)
        - minOrderQty check
        - Inactive instrument rejection
        """
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"))

        # Test 1: Min notional rejection
        is_ok, qty, reason = engine.validate_order_constraints("BTCUSDT", Decimal("0.001"), Decimal("1000.0"))
        # Notional = 0.001 * 1000 = $1.00 < $5.00 minNotional
        assert is_ok is False
        assert "minNotionalValue" in reason

        # Test 2: QtyStep flooring for SOLUSDT (step=0.1)
        is_ok, qty, reason = engine.validate_order_constraints("SOLUSDT", Decimal("2.385"), Decimal("150.0"))
        assert is_ok is True
        assert qty == Decimal("2.3")  # floored to 0.1 step!

        # Test 3: Min order qty rejection
        is_ok, qty, reason = engine.validate_order_constraints("SOLUSDT", Decimal("0.05"), Decimal("150.0"))
        assert is_ok is False
        assert "minOrderQty" in reason

        # Test 4: Inactive instrument rejection
        engine.instrument_rules["SOLUSDT"].status = "Delivering"
        is_ok, qty, reason = engine.validate_order_constraints("SOLUSDT", Decimal("5.0"), Decimal("150.0"))
        assert is_ok is False
        assert "Delivering" in reason
        engine.instrument_rules["SOLUSDT"].status = "Trading"

    # -------------------------------------------------------------------------
    # T9: Append-Only Financial Ledger & Balance Reconciliation
    # -------------------------------------------------------------------------
    def test_t09_repaired_financial_ledger_reconciliation(self):
        """
        T9 Repaired: FinancialLedger double-entry cashflow tracking, idempotency, and reconciliation.
        """
        ledger = FinancialLedger()
        initial_cap = Decimal("300.00")
        current_eq = initial_cap

        # Event 1: Entry fee deduction
        entry_fee = Decimal("0.30")
        current_eq -= entry_fee
        is_ok, ev1 = ledger.record_entry_fee("EXEC_ENTER_1", "BTCUSDT", "T1", entry_fee, current_eq)
        assert is_ok is True

        # Idempotency test on Event 1
        is_dup, _ = ledger.record_entry_fee("EXEC_ENTER_1", "BTCUSDT", "T1", entry_fee, current_eq)
        assert is_dup is False, "Duplicate exec_id must be ignored!"

        # Event 2: Partial TP slice
        gross_slice = Decimal("6.00")
        fee_slice = Decimal("0.05")
        current_eq += (gross_slice - fee_slice)
        is_ok, ev2 = ledger.record_slice("EXEC_SLICE_1", "BTCUSDT", "T1", 1, gross_slice, Decimal("0.15"), fee_slice, current_eq)
        assert is_ok is True

        # Event 3: Funding cashflow
        funding_cf = Decimal("0.04")
        current_eq += funding_cf
        is_ok, ev3 = ledger.record_funding("EXEC_FUND_1", "BTCUSDT", funding_cf, current_eq, Decimal("0.0001"), Decimal("400.0"), 1794000000000)
        assert is_ok is True

        # Reconcile
        is_balanced, expected_eq, curr_eq, disc = ledger.reconcile(initial_cap, current_eq)
        assert is_balanced is True
        assert disc == Decimal("0.0")

    # -------------------------------------------------------------------------
    # Auditor Task 10: Pre-Trade Risk Check (Blocks Entries on Breached Bar)
    # -------------------------------------------------------------------------
    def test_pre_trade_risk_halt_blocks_same_bar_entries(self):
        """
        Auditor Task 10: When a position incurs severe drawdown on the current bar
        that breaches DRAWDOWN_LIMIT_PCT, trading_halted must be set BEFORE new
        entries are evaluated, strictly preventing new entries on that same bar!
        """
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"))
        # Set artificial peak equity so that current drawdown is already near limit
        engine.peak_equity = Decimal("400.0")  # Peak was $400

        # Current open position: 1 Long BTC bought at 100
        current_prices = {"BTCUSDT": Decimal("100.0"), "ETHUSDT": Decimal("100.0"), "SOLUSDT": Decimal("100.0")}
        metrics = {"BTCUSDT": {"atr24": 2.0}, "ETHUSDT": {"atr24": 2.0}, "SOLUSDT": {"atr24": 2.0}}
        bar_ts = int(time.time() * 1000)
        engine._enter_leg(
            "BTCUSDT", 1, current_prices, metrics, engine.realized_equity, bar_ts,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=bar_ts, current_clock=bar_ts,
        )

        # On next bar, equity drops: BTC drops to 50.0 (catastrophic drop)
        # Total equity drops from 300 to ~250. Peak=400. Drawdown = (400 - 250)/400 = 37.5% > 30% limit!
        # At the same time, ETH and SOL show strong breakout candidates!
        btc_bars = [{'start': i*3600000, 'open': 100.0, 'high': 100.5, 'low': 99.5, 'close': 50.0 if i == 74 else 100.0, 'volume': 1000.0} for i in range(75)]
        eth_bars = [{'start': i*3600000, 'open': 100.0 + i*0.4, 'high': 100.0 + i*0.4 + 0.5, 'low': 100.0 + i*0.4 - 0.5, 'close': 100.0 + i*0.4, 'volume': 1000.0} for i in range(75)]
        sol_bars = [{'start': i*3600000, 'open': 100.0 - i*0.4, 'high': 100.0 - i*0.4 + 0.5, 'low': 100.0 - i*0.4 - 0.5, 'close': 100.0 - i*0.4, 'volume': 1000.0} for i in range(75)]

        prices_series = {"BTCUSDT": btc_bars, "ETHUSDT": eth_bars, "SOLUSDT": sol_bars}
        current_prices = {"BTCUSDT": Decimal("50.0"), "ETHUSDT": Decimal(str(eth_bars[-1]["close"])), "SOLUSDT": Decimal(str(sol_bars[-1]["close"]))}

        # Run process_bar
        engine.process_bar(prices_series, current_prices, bar_ts + 3600000)

        # Verify that trading_halted is True
        assert engine.trading_halted is True

        # CRITICAL INVARIANT: NO new positions were entered on that same bar!
        # BTC was stopped out, and ETH/SOL were NOT entered!
        assert len(engine.open_positions) == 0, (
            "Task 10 Violation: Engine opened new candidate positions on a bar where risk limit was breached!"
        )

    # -------------------------------------------------------------------------
    # T10: v3.8 No Synthetic APT Trade Generation
    # -------------------------------------------------------------------------
    def test_t10_repaired_v38_no_synthetic_apt_trade(self):
        """
        T10 Repaired: v3_8 paper_trader_v38.py has no hardcoded synthetic APT trade.
        """
        source_code = Path(REPO_ROOT / "v3_8" / "src" / "paper_trader_v38.py").read_text(encoding="utf-8")
        assert 'symbol="APTUSDT"' not in source_code
        assert "STAGE2_MAKER_TP" not in source_code

    # -------------------------------------------------------------------------
    # T12: Multi-Criteria Market Data Validity & Non-Blocking Protection
    # -------------------------------------------------------------------------
    def test_t12_repaired_market_data_validity_and_protective_exit(self):
        """
        T12 Repaired:
        1. Staleness check: age > 15s is rejected.
        2. Bid/Ask spread check: spread > 0.5% is rejected.
        3. Volatility jump: > 3.5x ATR freezes trailing advancement.
        4. Protective exit: If stop is breached, protective exit is NOT blocked
           when corroborated or confirmed.
        """
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"))
        validator = engine.market_validator
        atr = Decimal("2.0")

        # Test 1: Staleness check
        now_ts = 1794000020000
        old_ts = now_ts - 25000  # 25s old
        allow_adv, reason, allow_exit = validator.validate_quote(
            symbol="BTCUSDT", price=Decimal("100.0"), timestamp=old_ts, atr=atr, current_time=now_ts
        )
        assert allow_adv is False
        assert "Stale" in reason

        # Test 2: Wide bid/ask spread
        allow_adv, reason, allow_exit = validator.validate_quote(
            symbol="BTCUSDT", price=Decimal("100.0"), timestamp=now_ts, atr=atr,
            bid=Decimal("95.0"), ask=Decimal("105.0")  # 10% spread
        )
        assert allow_adv is False
        assert "Hollow book" in reason

        # Test 3: Volatility jump relative to previous valid price (3.5x ATR = 7.0)
        # Establish valid price of 100.0
        validator.validate_quote(symbol="BTCUSDT", price=Decimal("100.0"), timestamp=now_ts, atr=atr)

        # Spike to 110.0 (jump = 10.0 > 7.0)
        allow_adv, reason, allow_exit = validator.validate_quote(
            symbol="BTCUSDT", price=Decimal("110.0"), timestamp=now_ts + 1000, atr=atr
        )
        assert allow_adv is False  # trailing advancement frozen!
        assert "Volatility jump" in reason

        # Test 4: Non-blocking protective stop exit
        # Long position entered at 100 with stop at 95.0. Market drops sharply to 92.0.
        # First tick: unconfirmed breach
        allow_adv, reason, allow_exit_1 = validator.validate_quote(
            symbol="BTCUSDT", price=Decimal("92.0"), timestamp=now_ts + 2000, atr=atr,
            stop_price=Decimal("95.0"), direction=1
        )
        # Second tick: confirmed breach -> allow_exit MUST be True!
        allow_adv, reason, allow_exit_2 = validator.validate_quote(
            symbol="BTCUSDT", price=Decimal("92.0"), timestamp=now_ts + 3000, atr=atr,
            stop_price=Decimal("95.0"), direction=1
        )
        assert allow_exit_2 is True, "Protective exit must NOT be blocked on confirmed breach!"

    # -------------------------------------------------------------------------
    # T17: Broken Pair Leg Emergency Unwind
    # -------------------------------------------------------------------------
    def test_t17_repaired_broken_pair_leg_unwind(self):
        """
        T17 Repaired: When matched pair execution breaks (Long enters but Short fails),
        engine immediately unwinds the Long leg to restore portfolio neutrality.
        """
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"))
        current_prices = {"BTCUSDT": Decimal("100.0"), "ETHUSDT": Decimal("100.0")}
        metrics = {"BTCUSDT": {"atr24": 2.0}, "ETHUSDT": {"atr24": 2.0}}
        bar_ts = int(time.time() * 1000)

        # Simulate Long entered, but Short rejected due to exchange constraints
        engine._enter_leg(
            "BTCUSDT", 1, current_prices, metrics, engine.realized_equity, bar_ts,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=bar_ts, current_clock=bar_ts,
        )
        assert len(engine.open_positions) == 1

        # Unwind Long leg
        engine._unwind_leg("BTCUSDT", current_prices, bar_ts)

        assert len(engine.open_positions) == 0
        assert len(engine.closed_trades) == 1
        assert engine.closed_trades[0].reason == "UNWIND_BROKEN_PAIR"

    # -------------------------------------------------------------------------
    # Auditor Item 4: Candle Stress Stop-Gap vs Orderbook Fill Separation
    # -------------------------------------------------------------------------
    def test_candle_stress_vs_orderbook_fill_separation(self):
        """
        Auditor Item 4: Verifies distinct implementations of:
        1. execute_candle_stress_stop_gap (candle Open/Low gap penetration)
        2. execute_orderbook_fill (Bid/Ask orderbook fill)
        """
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"))
        pos = ActivePairPosition(
            symbol="BTCUSDT", direction=1, qty=Decimal("1.0"),
            entry_price=Decimal("100.0"), stop_price=Decimal("95.0"),
            initial_risk_dist=Decimal("5.0"), initial_atr=Decimal("2.0"),
            highest_p=Decimal("100.0"), lowest_p=Decimal("100.0"),
            entry_bar=0, entry_ts=0, entry_fee=Decimal("0.10")
        )

        # 1. Candle Stress Gap-Through
        is_hit, fill_p, mode = engine.execute_candle_stress_stop_gap(
            pos, bar_open=Decimal("90.0"), bar_low=Decimal("88.0"), bar_high=Decimal("91.0")
        )
        assert is_hit is True
        assert mode == "STOP_GAP_OPEN"
        assert fill_p == Decimal("90.0") * (Decimal("1.0") - engine.slippage)

        # 2. Orderbook Fill against Bid
        is_hit_ob, fill_p_ob, mode_ob = engine.execute_orderbook_fill(
            pos, bid=Decimal("94.50"), ask=Decimal("94.60"), last_trade=Decimal("94.55")
        )
        assert is_hit_ob is True
        assert mode_ob == "ORDERBOOK_STOP_FILL_BID"
        assert fill_p_ob == Decimal("94.50") * (Decimal("1.0") - engine.slippage)
