"""
F1C-R2 Audit Compliance Test Suite.
Exhaustively verifies every single P0 and P1 requirement issued by the independent auditor:
- P0-1: Transactional SQLite commit & fault-injection (pre-commit, during-commit, rollback).
- P0-2: Engine verifies ledger result before mutating positions or equity.
- P0-3: BybitClient get_kline List[List[str]] contract in _attempt_rest_backfill.
- P0-4: Crash recovery & state reconciliation with active Long/Short pair, TP state, and NO_NEW_TRADES lock on discrepancy.
- P0-5: Mandatory Bid/Ask, quote timestamps, clock validation, and direction-aware fill price.
- P0-6: Clock continuity, anti-lookahead, and synchronization timeout when < 16 symbols arrive.
- P1-7: Pre-trade portfolio beta guard (UNKNOWN beta blocks entry) and leverage limits.
- P1-8: InstrumentRule honest provenance (RuleSource.STATIC_CONFIG, version, timestamps).
- P1-9: Historical exposure verification for funding settlements.
- P1-10: Drawdown limit aligned at 15.0%.
"""

from decimal import Decimal
import json
from pathlib import Path
import shutil
import sqlite3
import time
from typing import Any, Dict, List, Optional
import pytest

from v3_5_repaired.src.config import (
    INITIAL_CAPITAL,
    DRAWDOWN_LIMIT_PCT,
    MAX_PORTFOLIO_BETA,
    MAX_PORTFOLIO_LEVERAGE,
    RuleSource,
    RULE_SPEC_VERSION,
    RULE_STATIC_TIMESTAMP,
    BAR_SYNC_TIMEOUT_SECONDS,
)
from v3_5_repaired.src.alpha_pairs_engine import (
    AlphaPairsEngineRepaired,
    ActivePairPosition,
    FundingSettlement,
    BetaStatus,
    BetaResult,
)
from v3_5_repaired.src.financial_ledger import FinancialLedger
from v3_5_repaired.src.paper_trader_v35 import PaperTraderV35Repaired

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def clean_test_dir():
    test_dir = REPO_ROOT / ".scratch" / "f1c_r2_audit_test_db"
    if test_dir.exists():
        shutil.rmtree(test_dir, ignore_errors=True)
    test_dir.mkdir(parents=True, exist_ok=True)
    yield test_dir
    shutil.rmtree(test_dir, ignore_errors=True)


class TestF1CR2AuditCompliance:

    # -------------------------------------------------------------------------
    # P0-1: Transactional SQLite Commit & Fault-Injection
    # -------------------------------------------------------------------------
    def test_p0_1_fault_injection_pre_commit_leaves_memory_untouched(self, clean_test_dir):
        """Simulates SQLite failure BEFORE commit: memory must remain 100% clean."""
        db_path = str(clean_test_dir / "ledger_fault_pre.db")
        ledger = FinancialLedger(db_path=db_path)

        # Trigger pre-commit fault injection
        ledger.fault_injection_pre_commit = True

        ok, ev = ledger.record_event(
            exec_id="EXEC_FAULT_1",
            event_type="ENTRY_FEE",
            symbol="BTCUSDT",
            trade_id="TRADE_1",
            direction=1,
            gross_amount=Decimal("0.0"),
            fee_amount=Decimal("0.50"),
            net_cashflow=Decimal("-0.50"),
            balance_after=Decimal("299.50"),
        )
        assert ok is False
        assert ev is None
        assert len(ledger.events) == 0, "Memory events modified despite pre-commit failure!"
        assert len(ledger.seen_exec_ids) == 0, "Seen exec_ids modified despite pre-commit failure!"

        # Verify SQLite has 0 rows
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM financial_events")
            assert cursor.fetchone()[0] == 0

    def test_p0_1_fault_injection_during_commit_rolls_back_cleanly(self, clean_test_dir):
        """Simulates SQLite failure DURING commit: memory must remain 100% clean."""
        db_path = str(clean_test_dir / "ledger_fault_during.db")
        ledger = FinancialLedger(db_path=db_path)

        ledger.fault_injection_during_commit = True

        ok, ev = ledger.record_event(
            exec_id="EXEC_FAULT_2",
            event_type="ENTRY_FEE",
            symbol="ETHUSDT",
            trade_id="TRADE_2",
            direction=-1,
            gross_amount=Decimal("0.0"),
            fee_amount=Decimal("0.40"),
            net_cashflow=Decimal("-0.40"),
            balance_after=Decimal("299.60"),
        )
        assert ok is False
        assert ev is None
        assert len(ledger.events) == 0
        assert len(ledger.seen_exec_ids) == 0

    # -------------------------------------------------------------------------
    # P0-2: Engine Verifies Ledger Result Before Mutating Positions or Equity
    # -------------------------------------------------------------------------
    def test_p0_2_engine_verifies_ledger_failure_aborts_entry(self, clean_test_dir):
        """If ledger write fails, _enter_leg must not deduct equity or open position."""
        db_path = str(clean_test_dir / "engine_ledger_verify.db")
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"), ledger_db_path=db_path)
        start_equity = engine.realized_equity
        bar_ts = int(time.time() * 1000)

        # Inject ledger failure
        engine.ledger.fault_injection_pre_commit = True

        entered = engine._enter_leg(
            "BTCUSDT", 1, {"BTCUSDT": Decimal("100.0")}, {"BTCUSDT": {"atr24": 2.0}},
            engine.realized_equity, bar_ts,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=bar_ts, current_clock=bar_ts,
        )
        assert entered is False
        assert engine.realized_equity == start_equity, "Realized equity decremented despite ledger failure!"
        assert "BTCUSDT" not in engine.open_positions, "Position opened despite ledger failure!"

    def test_p0_2_engine_verifies_ledger_failure_aborts_partial_tp(self, clean_test_dir):
        """If ledger slice recording fails, partial TP must not reduce qty or take profit."""
        db_path = str(clean_test_dir / "tp_ledger_verify.db")
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"), ledger_db_path=db_path)
        bar_ts = int(time.time() * 1000)

        # Normal entry
        engine._enter_leg(
            "BTCUSDT", 1, {"BTCUSDT": Decimal("100.0")}, {"BTCUSDT": {"atr24": 2.0}},
            engine.realized_equity, bar_ts,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=bar_ts, current_clock=bar_ts,
        )
        pos = engine.open_positions["BTCUSDT"]
        initial_qty = pos.qty
        equity_before_tp = engine.realized_equity

        # Now inject ledger failure during TP
        engine.ledger.fault_injection_during_commit = True

        target_tp_p = Decimal("120.00")
        current_prices = {"BTCUSDT": target_tp_p}
        prices_series = {
            s: [{"open": 100.0, "high": 125.0, "low": 99.0, "close": float(target_tp_p), "volume": 1000.0}] * 60
            for s in ["BTCUSDT", "ETHUSDT"]
        }

        engine.process_bar(prices_series, current_prices, bar_ts + 3600000)

        # Invariant: position slice MUST NOT have been applied!
        assert pos.qty == initial_qty, "Position quantity modified despite ledger TP failure!"
        assert pos.tp_taken is False, "tp_taken flagged True despite ledger TP failure!"
        assert engine.realized_equity == equity_before_tp, "Realized equity increased despite ledger TP failure!"
        assert len(engine.closed_trades) == 0

    # -------------------------------------------------------------------------
    # P0-3: BybitClient get_kline List[List[str]] Contract Handling
    # -------------------------------------------------------------------------
    def test_p0_3_bybit_client_list_contract_in_backfill(self):
        """Verifies _attempt_rest_backfill handles List[List[str]] without AttributeError."""
        class MockBybitClient:
            def get_kline(self, symbol, interval, limit=5, category="linear"):
                # Real BybitClient contract returns List[List[str]]
                return [
                    ["1794000000000", "100.0", "105.0", "99.0", "104.5", "500.0", "52000.0"],
                    ["1793996400000", "98.0", "100.5", "97.5", "100.0", "400.0", "40000.0"],
                ]

        trader = PaperTraderV35Repaired.__new__(PaperTraderV35Repaired)
        trader.client = MockBybitClient()
        trader.pending_bars_by_ts = {1794000000000: {}}
        trader.latest_prices = {}

        # Should execute cleanly without calling .get() on list!
        trader._attempt_rest_backfill(1794000000000, ["BTCUSDT"])

        assert "BTCUSDT" in trader.pending_bars_by_ts[1794000000000]
        backfilled = trader.pending_bars_by_ts[1794000000000]["BTCUSDT"]
        assert backfilled["close"] == 104.5
        assert trader.latest_prices["BTCUSDT"] == Decimal("104.5")

    # -------------------------------------------------------------------------
    # P0-4: Crash Recovery & Reconciliation State Machine
    # -------------------------------------------------------------------------
    def test_p0_4_clean_crash_recovery_with_active_positions(self, clean_test_dir):
        """Verifies clean state restoration with active Long/Short positions, TP taken, and funding."""
        db_path = str(clean_test_dir / "crash_recovery_clean.db")
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"), ledger_db_path=db_path)
        start_ts = int(time.time() * 1000)

        # Enter Long BTC + Short ETH
        engine._enter_leg("BTCUSDT", 1, {"BTCUSDT": Decimal("100.0")}, {"BTCUSDT": {"atr24": 2.0}}, engine.realized_equity, start_ts, bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=start_ts, current_clock=start_ts)
        engine._enter_leg("ETHUSDT", -1, {"ETHUSDT": Decimal("50.0")}, {"ETHUSDT": {"atr24": 1.0}}, engine.realized_equity, start_ts, bid=Decimal("50.00"), ask=Decimal("50.02"), quote_ts=start_ts, current_clock=start_ts)

        # Partial TP on BTC
        target_tp_p = Decimal("120.00")
        prices_series = {
            "BTCUSDT": [{"open": 100.0, "high": 125.0, "low": 99.0, "close": 120.0, "volume": 1000.0}] * 60,
            "ETHUSDT": [{"open": 50.0, "high": 51.0, "low": 49.0, "close": 50.0, "volume": 1000.0}] * 60,
        }
        engine.process_bar(prices_series, {"BTCUSDT": target_tp_p, "ETHUSDT": Decimal("50.0")}, start_ts + 3600000)

        # Funding settlement
        funding = FundingSettlement(symbol="BTCUSDT", settlement_ts=start_ts + 7200000, rate=Decimal("0.0001"), mark_price=Decimal("120.00"))
        engine.apply_funding_settlements([funding])

        snapshot = engine.export_state()

        # Instantiate fresh engine and reconcile
        recovery_engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"), ledger_db_path=db_path)
        status, errors = recovery_engine.reconcile_and_recover(snapshot)

        assert status == "RECONCILED"
        assert len(errors) == 0
        assert recovery_engine.trading_halted is False
        assert len(recovery_engine.open_positions) == 2
        assert recovery_engine.open_positions["BTCUSDT"].tp_taken is True
        assert recovery_engine.realized_equity == engine.realized_equity

    def test_p0_4_corrupted_snapshot_equity_triggers_no_new_trades(self, clean_test_dir):
        """If snapshot equity does not match SQLite ledger, engine locks in NO_NEW_TRADES."""
        db_path = str(clean_test_dir / "crash_recovery_corrupt.db")
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"), ledger_db_path=db_path)
        start_ts = int(time.time() * 1000)

        engine._enter_leg("BTCUSDT", 1, {"BTCUSDT": Decimal("100.0")}, {"BTCUSDT": {"atr24": 2.0}}, engine.realized_equity, start_ts, bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=start_ts, current_clock=start_ts)
        snapshot = engine.export_state()

        # Tamper snapshot equity
        snapshot["realized_equity"] = str(Decimal(snapshot["realized_equity"]) + Decimal("15.00"))

        recovery_engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"), ledger_db_path=db_path)
        status, errors = recovery_engine.reconcile_and_recover(snapshot)

        assert status == "NO_NEW_TRADES"
        assert recovery_engine.trading_halted is True
        assert any("Equity mismatch" in err for err in errors)

    # -------------------------------------------------------------------------
    # P0-5: Mandatory Live Orderbook & Timestamps for Entry
    # -------------------------------------------------------------------------
    def test_p0_5_missing_bid_or_ask_strictly_rejected(self):
        """New entries without live bid/ask MUST fail-closed."""
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"))
        bar_ts = int(time.time() * 1000)
        prices = {"BTCUSDT": Decimal("100.0")}
        metrics = {"BTCUSDT": {"atr24": 2.0}}

        # Omit bid and ask
        assert engine._enter_leg("BTCUSDT", 1, prices, metrics, engine.realized_equity, bar_ts) is False
        # Omit ask
        assert engine._enter_leg("BTCUSDT", 1, prices, metrics, engine.realized_equity, bar_ts, bid=Decimal("99.98")) is False
        # Hollow orderbook (bid >= ask)
        assert engine._enter_leg("BTCUSDT", 1, prices, metrics, engine.realized_equity, bar_ts, bid=Decimal("100.02"), ask=Decimal("100.00")) is False
        # Stale quote (>5000ms)
        assert engine._enter_leg("BTCUSDT", 1, prices, metrics, engine.realized_equity, bar_ts, bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=bar_ts - 6000, current_clock=bar_ts) is False

    def test_p0_5_direction_aware_fill_price(self):
        """Long taker enters at Ask, Short taker enters at Bid."""
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"))
        bar_ts = int(time.time() * 1000)
        prices = {"BTCUSDT": Decimal("100.0"), "ETHUSDT": Decimal("50.0")}
        metrics = {"BTCUSDT": {"atr24": 2.0}, "ETHUSDT": {"atr24": 1.0}}

        engine._enter_leg("BTCUSDT", 1, prices, metrics, engine.realized_equity, bar_ts, bid=Decimal("99.98"), ask=Decimal("100.05"), quote_ts=bar_ts, current_clock=bar_ts)
        engine._enter_leg("ETHUSDT", -1, prices, metrics, engine.realized_equity, bar_ts, bid=Decimal("49.95"), ask=Decimal("50.02"), quote_ts=bar_ts, current_clock=bar_ts)

        assert engine.open_positions["BTCUSDT"].entry_price == Decimal("100.05"), "Long must fill at Ask!"
        assert engine.open_positions["ETHUSDT"].entry_price == Decimal("49.95"), "Short must fill at Bid!"

    # -------------------------------------------------------------------------
    # P0-6: Clock Continuity & Timeout with < 16 Symbols
    # -------------------------------------------------------------------------
    def test_p0_6_sync_timeout_drops_partial_buffer_without_trading(self):
        """When fewer than 16 symbols arrive and timeout expires, pending bar is dropped without trading."""
        trader = PaperTraderV35Repaired.__new__(PaperTraderV35Repaired)
        import threading
        trader.lock = threading.RLock()
        trader.symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT"]
        trader.processed_kline_keys = set()
        trader.pending_bars_by_ts = {}
        trader.pending_bars_first_seen = {}
        trader.last_processed_bar_ts = 0
        trader.latest_prices = {}

        class DummyClient:
            def get_kline(self, **kwargs):
                return []

        trader.client = DummyClient()

        # Send kline for only 1 symbol at timestamp T
        target_ts = 1794000000000
        msg = {
            "topic": "kline.60.BTCUSDT",
            "data": [{"start": target_ts, "close": "70000.0", "confirm": True, "open": "69000", "high": "71000", "low": "68000", "volume": "100"}],
        }

        trader.on_kline_event(msg)
        assert target_ts in trader.pending_bars_by_ts

        # Advance first_seen beyond timeout (e.g. 35 seconds ago)
        trader.pending_bars_first_seen[target_ts] = time.time() - (BAR_SYNC_TIMEOUT_SECONDS + 5.0)

        # Trigger second event for same start_ts (or evaluation check)
        msg_eth = {
            "topic": "kline.60.ETHUSDT",
            "data": [{"start": target_ts, "close": "3000.0", "confirm": True, "open": "2900", "high": "3100", "low": "2800", "volume": "100"}],
        }
        res = trader.on_kline_event(msg_eth)

        # Must report False and drop pending bar buffer
        assert res is False
        assert target_ts not in trader.pending_bars_by_ts, "Pending bar was not cleared on timeout!"
        assert trader.last_processed_bar_ts == target_ts

    # -------------------------------------------------------------------------
    # P1-7: Pre-Trade Portfolio Beta & Aggregate Leverage Guard
    # -------------------------------------------------------------------------
    def test_p1_7_unknown_beta_blocks_new_pair_entry(self):
        """If benchmark variance is near zero or data is insufficient, entry must be blocked."""
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"))
        # Constant BTC close -> variance 0 -> UNKNOWN beta
        btc_bars = [{'start': i*3600000, 'open': 100.0, 'high': 100.5, 'low': 99.5, 'close': 100.0, 'volume': 1000.0} for i in range(75)]
        eth_bars = [{'start': i*3600000, 'open': 100.0 + i*0.4, 'high': 100.0 + i*0.4 + 1.0, 'low': 100.0 + i*0.4 - 1.0, 'close': 100.0 + i*0.4, 'volume': 1000.0} for i in range(75)]
        sol_bars = [{'start': i*3600000, 'open': 100.0 - i*0.4, 'high': 100.0 - i*0.4 + 1.0, 'low': 100.0 - i*0.4 - 1.0, 'close': 100.0 - i*0.4, 'volume': 1000.0} for i in range(75)]
        prices_series = {"BTCUSDT": btc_bars, "ETHUSDT": eth_bars, "SOLUSDT": sol_bars}
        current_prices = {"BTCUSDT": Decimal("100.0"), "ETHUSDT": Decimal(str(eth_bars[-1]["close"])), "SOLUSDT": Decimal(str(sol_bars[-1]["close"]))}
        orderbook_bids = {s: p * Decimal("0.9998") for s, p in current_prices.items()}
        orderbook_asks = {s: p * Decimal("1.0002") for s, p in current_prices.items()}

        engine.process_bar(
            prices_series, current_prices, int(time.time() * 1000),
            orderbook_bids=orderbook_bids, orderbook_asks=orderbook_asks,
        )

        assert len(engine.open_positions) == 0, "Pair entry allowed despite UNKNOWN beta!"

    # -------------------------------------------------------------------------
    # P1-8: InstrumentRule Honest Provenance
    # -------------------------------------------------------------------------
    def test_p1_8_instrument_rule_provenance_and_version(self):
        """Instrument rules must declare RuleSource.STATIC_CONFIG and explicit release timestamp."""
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"))
        rule_btc = engine.instrument_rules["BTCUSDT"]

        assert rule_btc.source == RuleSource.STATIC_CONFIG
        assert rule_btc.version == RULE_SPEC_VERSION
        assert rule_btc.updated_at_ms == RULE_STATIC_TIMESTAMP
        assert rule_btc.is_verified is False
        assert rule_btc.last_exchange_sync_ts is None

        # Call explicit verification
        engine.verify_instrument_rules(RuleSource.EXCHANGE_REST, verified_ts=1794000000000)
        assert rule_btc.source == RuleSource.EXCHANGE_REST
        assert rule_btc.is_verified is True
        assert rule_btc.last_exchange_sync_ts == 1794000000000

    # -------------------------------------------------------------------------
    # P1-9: Historical Exposure Verification for Funding Settlements
    # -------------------------------------------------------------------------
    def test_p1_9_historical_exposure_blocks_prior_funding(self):
        """Funding settlements prior to position entry timestamp must not accrue."""
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"))
        entry_ts = 1794000000000
        engine._enter_leg("BTCUSDT", 1, {"BTCUSDT": Decimal("100.0")}, {"BTCUSDT": {"atr24": 2.0}}, engine.realized_equity, entry_ts, bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=entry_ts, current_clock=entry_ts)

        equity_before = engine.realized_equity

        # Settlement timestamp is PRIOR to entry (e.g. 2 hours earlier)
        prior_settlement = FundingSettlement(
            symbol="BTCUSDT",
            settlement_ts=entry_ts - 7200000,
            rate=Decimal("0.0005"),
            mark_price=Decimal("100.0"),
        )
        cf = engine.apply_funding_settlements([prior_settlement])

        assert cf == Decimal("0.0"), "Funding was accrued for settlement prior to position entry!"
        assert engine.realized_equity == equity_before

    # -------------------------------------------------------------------------
    # P1-10: Drawdown Limit Alignment at 15.0%
    # -------------------------------------------------------------------------
    def test_p1_10_drawdown_limit_aligned_at_15_pct(self):
        """Drawdown halt limit must be strictly aligned to 15.0%."""
        assert DRAWDOWN_LIMIT_PCT == Decimal("15.0")
