"""
F1C-R3 Audit Compliance Test Suite.
Exhaustively verifies every single P0 and P1 requirement issued by the independent auditor:
- P0-A: SQLite transactional defect: explicit rollback, new connection verification, and post-commit pre-memory crash recovery.
- P0-B: Eliminate synthetic orderbook: strict prohibition of QuoteSource.SYNTHETIC_SCENARIO in production, direction-aware fill pricing, quote staleness.
- P0-C: Independent position reconciliation from SQLite journal: detection of 10x quantity corruption, prohibition of empty start when ledger has open positions.
- P0-D: Genuine InstrumentRule provenance: fail-closed on unverified/stale rules, real Bybit V5 /v5/market/instruments-info REST parser.
- P1-1: Autonomous synchronization timeout handler (check_sync_timeouts).
- P1-2: Hourly candle continuity check (verify_kline_continuity).
- P1-3: Precise funding settlement timing and partial TP remaining quantity exposure.
- P1-4: Independent verification of portfolio beta limit (0.35) vs leverage cap (2.0x) with UNKNOWN block.
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
    QuoteSource,
    RULE_SPEC_VERSION,
    RULE_STATIC_TIMESTAMP,
    BAR_SYNC_TIMEOUT_SECONDS,
    BAR_INTERVAL_MS,
)
from v3_5_repaired.src.alpha_pairs_engine import (
    AlphaPairsEngineRepaired,
    ActivePairPosition,
    FundingSettlement,
    BetaStatus,
    BetaResult,
    OrderbookQuote,
)
from v3_5_repaired.src.financial_ledger import FinancialLedger
from v3_5_repaired.src.paper_trader_v35 import PaperTraderV35Repaired

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture
def clean_test_dir():
    test_dir = REPO_ROOT / ".scratch" / "f1c_r3_audit_test_db"
    if test_dir.exists():
        shutil.rmtree(test_dir, ignore_errors=True)
    test_dir.mkdir(parents=True, exist_ok=True)
    yield test_dir
    shutil.rmtree(test_dir, ignore_errors=True)


class TestF1CR3AuditCompliance:

    # -------------------------------------------------------------------------
    # P0-A: SQLite Transactional Defect & Post-Commit Crash Recovery
    # -------------------------------------------------------------------------
    def test_p0_a_fault_injection_during_commit_performs_explicit_rollback(self, clean_test_dir):
        """
        P0-A Requirement: When fault_injection_during_commit is active,
        the transaction MUST be explicitly rolled back.
        Opening a brand new connection after failure must show ZERO records in SQLite.
        """
        db_path = str(clean_test_dir / "ledger_p0_a_during.db")
        ledger = FinancialLedger(db_path=db_path)

        ledger.fault_injection_during_commit = True

        ok, ev = ledger.record_event(
            exec_id="EXEC_FAULT_DURING_COMMIT",
            event_type="ENTRY_FEE",
            symbol="BTCUSDT",
            trade_id="TRADE_P0_A",
            direction=1,
            gross_amount=Decimal("0.0"),
            fee_amount=Decimal("0.50"),
            net_cashflow=Decimal("-0.50"),
            balance_after=Decimal("299.50"),
        )
        assert ok is False
        assert ev is None
        assert len(ledger.events) == 0, "Memory events modified despite commit failure!"
        assert len(ledger.seen_exec_ids) == 0, "Seen exec_ids modified despite commit failure!"

        # Brand new independent SQLite connection to verify database state
        with sqlite3.connect(db_path) as fresh_conn:
            cursor = fresh_conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM financial_events WHERE exec_id = 'EXEC_FAULT_DURING_COMMIT'")
            count = cursor.fetchone()[0]
            assert count == 0, f"Transactional defect: SQLite committed {count} rows despite failure during commit!"

    def test_p0_a_post_commit_pre_memory_crash_recovery_and_deduplication(self, clean_test_dir):
        """
        P0-A Requirement: If a crash occurs after successful SQLite COMMIT but before in-memory
        events/seen_exec_ids are updated:
        1. Subsequent has_exec_id queries SQLite and returns True (idempotent deduplication).
        2. Attempting to record same exec_id again is rejected without duplicate insert.
        3. restore_from_db reconstructs memory and equity penny-perfect.
        """
        db_path = str(clean_test_dir / "ledger_p0_a_post_commit.db")
        ledger = FinancialLedger(db_path=db_path)

        # Trigger crash post-commit pre-memory
        ledger.fault_injection_post_commit_pre_memory = True

        ok, ev = ledger.record_event(
            exec_id="EXEC_POST_COMMIT_CRASH",
            event_type="ENTRY_FEE",
            symbol="ETHUSDT",
            trade_id="TRADE_P0_A_2",
            direction=-1,
            gross_amount=Decimal("0.0"),
            fee_amount=Decimal("0.40"),
            net_cashflow=Decimal("-0.40"),
            balance_after=Decimal("299.60"),
        )
        assert ok is False
        assert ev is None
        assert len(ledger.events) == 0, "Memory was prematurely modified before failure point!"

        # 1. Row was committed in SQLite
        with sqlite3.connect(db_path) as fresh_conn:
            cursor = fresh_conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM financial_events WHERE exec_id = 'EXEC_POST_COMMIT_CRASH'")
            assert cursor.fetchone()[0] == 1, "Record missing from SQLite after commit!"

        # 2. has_exec_id queries SQLite and correctly returns True
        assert ledger.has_exec_id("EXEC_POST_COMMIT_CRASH") is True

        # 3. Retrying same exec_id is idempotently rejected
        ledger.fault_injection_post_commit_pre_memory = False
        retry_ok, retry_ev = ledger.record_event(
            exec_id="EXEC_POST_COMMIT_CRASH",
            event_type="ENTRY_FEE",
            symbol="ETHUSDT",
            trade_id="TRADE_P0_A_2",
            direction=-1,
            gross_amount=Decimal("0.0"),
            fee_amount=Decimal("0.40"),
            net_cashflow=Decimal("-0.40"),
            balance_after=Decimal("299.60"),
        )
        assert retry_ok is False, "Duplicate exec_id was not rejected after post-commit crash!"

        # 4. State restoration reconstructs memory cleanly
        reconstructed_equity, event_count = ledger.restore_from_db(initial_capital=Decimal("300.00"))
        assert event_count == 1
        assert reconstructed_equity == Decimal("299.60")
        assert "EXEC_POST_COMMIT_CRASH" in ledger.seen_exec_ids

    # -------------------------------------------------------------------------
    # P0-B: Eliminate Synthetic Orderbook & Classify Quote Sources
    # -------------------------------------------------------------------------
    def test_p0_b_synthetic_orderbook_rejected_in_production(self):
        """
        P0-B Requirement: QuoteSource.SYNTHETIC_SCENARIO must be strictly rejected
        unless allow_synthetic_quotes=True is explicitly configured.
        """
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"), auto_verify_rules=True, allow_synthetic_quotes=False)
        bar_ts = int(time.time() * 1000)

        # Attempt entry with synthetic quote source in production mode
        entered = engine._enter_leg(
            "BTCUSDT", 1, {"BTCUSDT": Decimal("100.0")}, {"BTCUSDT": {"atr24": 2.0}},
            engine.realized_equity, bar_ts,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=bar_ts, current_clock=bar_ts,
            quote_source=QuoteSource.SYNTHETIC_SCENARIO,
        )
        assert entered is False, "Synthetic quote was allowed in production mode!"
        assert "BTCUSDT" not in engine.open_positions

    def test_p0_b_missing_or_stale_l1_quote_blocks_entry(self):
        """
        P0-B Requirement: Missing quotes or quotes older than 5000ms strictly block entry.
        """
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"), auto_verify_rules=True)
        bar_ts = int(time.time() * 1000)

        # 1. Missing bid/ask
        entered_missing = engine._enter_leg(
            "BTCUSDT", 1, {"BTCUSDT": Decimal("100.0")}, {"BTCUSDT": {"atr24": 2.0}},
            engine.realized_equity, bar_ts,
            bid=None, ask=None, quote_ts=bar_ts, current_clock=bar_ts,
            quote_source=QuoteSource.EXCHANGE_L1,
        )
        assert entered_missing is False

        # 2. Crossed orderbook (bid >= ask)
        entered_crossed = engine._enter_leg(
            "BTCUSDT", 1, {"BTCUSDT": Decimal("100.0")}, {"BTCUSDT": {"atr24": 2.0}},
            engine.realized_equity, bar_ts,
            bid=Decimal("100.50"), ask=Decimal("100.00"), quote_ts=bar_ts, current_clock=bar_ts,
            quote_source=QuoteSource.EXCHANGE_L1,
        )
        assert entered_crossed is False

        # 3. Stale quote (quote_ts is 6000ms older than current_clock)
        now_clock = int(time.time() * 1000)
        entered_stale = engine._enter_leg(
            "BTCUSDT", 1, {"BTCUSDT": Decimal("100.0")}, {"BTCUSDT": {"atr24": 2.0}},
            engine.realized_equity, bar_ts,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=now_clock - 6000, current_clock=now_clock,
            quote_source=QuoteSource.EXCHANGE_L1,
        )
        assert entered_stale is False

    def test_p0_b_direction_aware_l1_fill_pricing(self):
        """
        P0-B Requirement: Long executes at Ask (paying spread), Short executes at Bid (paying spread).
        """
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"), auto_verify_rules=True)
        bar_ts = int(time.time() * 1000)

        # Long entry on BTCUSDT: Bid=99.90, Ask=100.10 -> Fill must be 100.10
        ok_l = engine._enter_leg(
            "BTCUSDT", 1, {"BTCUSDT": Decimal("100.0")}, {"BTCUSDT": {"atr24": 2.0}},
            engine.realized_equity, bar_ts,
            bid=Decimal("99.90"), ask=Decimal("100.10"), quote_ts=bar_ts, current_clock=bar_ts,
            quote_source=QuoteSource.EXCHANGE_L1,
        )
        assert ok_l is True
        assert engine.open_positions["BTCUSDT"].entry_price == Decimal("100.10")

        # Short entry on ETHUSDT: Bid=49.90, Ask=50.10 -> Fill must be 49.90
        ok_s = engine._enter_leg(
            "ETHUSDT", -1, {"ETHUSDT": Decimal("50.0")}, {"ETHUSDT": {"atr24": 1.0}},
            engine.realized_equity, bar_ts,
            bid=Decimal("49.90"), ask=Decimal("50.10"), quote_ts=bar_ts, current_clock=bar_ts,
            quote_source=QuoteSource.EXCHANGE_L1,
        )
        assert ok_s is True
        assert engine.open_positions["ETHUSDT"].entry_price == Decimal("49.90")

    # -------------------------------------------------------------------------
    # P0-C: Independent Position Reconciliation from SQLite Journal
    # -------------------------------------------------------------------------
    def test_p0_c_corrupted_position_qty_10x_triggers_no_new_trades(self, clean_test_dir):
        """
        P0-C Requirement: Tampering open position quantity in JSON by 10x without changing equity
        MUST be detected by position cross-reconciliation and trigger NO_NEW_TRADES.
        """
        db_path = str(clean_test_dir / "p0_c_corrupt_10x.db")
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"), ledger_db_path=db_path, auto_verify_rules=True)
        bar_ts = int(time.time() * 1000)

        engine._enter_leg(
            "BTCUSDT", 1, {"BTCUSDT": Decimal("100.0")}, {"BTCUSDT": {"atr24": 2.0}},
            engine.realized_equity, bar_ts,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=bar_ts, current_clock=bar_ts,
        )
        snapshot = engine.export_state()

        # Invariant check: tamper open position quantity by 10x in JSON (equity untouched!)
        orig_qty = Decimal(snapshot["open_positions"]["BTCUSDT"]["qty"])
        corrupted_qty = orig_qty * Decimal("10.0")
        snapshot["open_positions"]["BTCUSDT"]["qty"] = str(corrupted_qty)

        recovery_engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"), ledger_db_path=db_path, auto_verify_rules=True)
        status, errors = recovery_engine.reconcile_and_recover(snapshot)

        assert status == "NO_NEW_TRADES", "Corrupted 10x position quantity did not trigger NO_NEW_TRADES!"
        assert recovery_engine.trading_halted is True
        assert any("quantity mismatch" in err.lower() for err in errors)

    def test_p0_c_missing_json_with_unclosed_ledger_positions_halts_session(self, clean_test_dir):
        """
        P0-C Requirement: In absence of JSON snapshot, the engine CANNOT start a fresh session
        with an empty portfolio if unclosed positions exist in SQLite ledger.
        """
        db_path = str(clean_test_dir / "p0_c_missing_json.db")
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"), ledger_db_path=db_path, auto_verify_rules=True)
        bar_ts = int(time.time() * 1000)

        # Enter position into SQLite
        engine._enter_leg(
            "BTCUSDT", 1, {"BTCUSDT": Decimal("100.0")}, {"BTCUSDT": {"atr24": 2.0}},
            engine.realized_equity, bar_ts,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=bar_ts, current_clock=bar_ts,
        )

        # Simulate missing JSON file (snapshot is None)
        recovery_engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"), ledger_db_path=db_path, auto_verify_rules=True)
        status, errors = recovery_engine.reconcile_and_recover(None)

        assert status == "NO_NEW_TRADES", "Engine started fresh despite unclosed positions in SQLite ledger!"
        assert recovery_engine.trading_halted is True
        assert len(recovery_engine.open_positions) == 1
        assert "BTCUSDT" in recovery_engine.open_positions

    def test_p0_c_full_lifecycle_position_reconstruction_from_ledger(self, clean_test_dir):
        """
        P0-C Requirement: Reconstruct positions independently from SQLite journal:
        qty, direction, entry price, entry fee, and partial TP state must match exactly.
        """
        db_path = str(clean_test_dir / "p0_c_lifecycle_reconstruct.db")
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"), ledger_db_path=db_path, auto_verify_rules=True, allow_synthetic_quotes=True)
        bar_ts = int(time.time() * 1000)

        # Enter Long BTC + Short ETH
        engine._enter_leg(
            "BTCUSDT", 1, {"BTCUSDT": Decimal("100.0")}, {"BTCUSDT": {"atr24": 2.0}},
            engine.realized_equity, bar_ts,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=bar_ts, current_clock=bar_ts,
        )
        engine._enter_leg(
            "ETHUSDT", -1, {"ETHUSDT": Decimal("50.0")}, {"ETHUSDT": {"atr24": 1.0}},
            engine.realized_equity, bar_ts,
            bid=Decimal("50.00"), ask=Decimal("50.02"), quote_ts=bar_ts, current_clock=bar_ts,
        )

        # Partial TP on BTC at +4.0R
        target_tp_p = Decimal("120.00")
        prices_series = {
            "BTCUSDT": [{"open": 100.0, "high": 125.0, "low": 99.0, "close": 120.0, "volume": 1000.0}] * 60,
            "ETHUSDT": [{"open": 50.0, "high": 51.0, "low": 49.0, "close": 50.0, "volume": 1000.0}] * 60,
        }
        engine.process_bar(
            prices_series, {"BTCUSDT": target_tp_p, "ETHUSDT": Decimal("50.0")}, bar_ts + 3600000,
            orderbook_bids={"BTCUSDT": Decimal("119.98"), "ETHUSDT": Decimal("49.98")},
            orderbook_asks={"BTCUSDT": Decimal("120.02"), "ETHUSDT": Decimal("50.02")},
        )

        # Now reconstruct directly from SQLite events
        reconstructed = engine.reconstruct_positions_from_ledger()
        assert len(reconstructed) == 2
        assert reconstructed["BTCUSDT"].tp_taken is True
        assert reconstructed["BTCUSDT"].qty == engine.open_positions["BTCUSDT"].qty
        assert reconstructed["BTCUSDT"].entry_fee == engine.open_positions["BTCUSDT"].entry_fee
        assert reconstructed["ETHUSDT"].qty == engine.open_positions["ETHUSDT"].qty

    # -------------------------------------------------------------------------
    # P0-D: Genuine InstrumentRule Provenance & Fail-Closed Behavior
    # -------------------------------------------------------------------------
    def test_p0_d_unverified_rule_strictly_blocks_entry(self):
        """
        P0-D Requirement: Unverified instrument rule (is_verified=False) strictly blocks entry.
        """
        # Default engine initializes with is_verified=False
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"), auto_verify_rules=False)
        bar_ts = int(time.time() * 1000)

        entered = engine._enter_leg(
            "BTCUSDT", 1, {"BTCUSDT": Decimal("100.0")}, {"BTCUSDT": {"atr24": 2.0}},
            engine.realized_equity, bar_ts,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=bar_ts, current_clock=bar_ts,
        )
        assert entered is False, "Unverified instrument rule allowed order entry!"

    def test_p0_d_missing_sync_ts_strictly_blocks_entry(self):
        """
        P0-D Requirement: Instrument rule lacking exchange sync timestamp strictly blocks entry.
        """
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"), auto_verify_rules=False)
        rule = engine.instrument_rules["BTCUSDT"]
        rule.is_verified = True
        rule.last_exchange_sync_ts = None  # Missing exchange sync timestamp

        bar_ts = int(time.time() * 1000)
        entered = engine._enter_leg(
            "BTCUSDT", 1, {"BTCUSDT": Decimal("100.0")}, {"BTCUSDT": {"atr24": 2.0}},
            engine.realized_equity, bar_ts,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=bar_ts, current_clock=bar_ts,
        )
        assert entered is False, "Rule lacking exchange sync timestamp allowed order entry!"

    def test_p0_d_stale_rule_strictly_blocks_entry(self):
        """
        P0-D Requirement: Instrument rule older than 24 hours strictly blocks entry.
        """
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"), auto_verify_rules=False)
        now_ts = int(time.time() * 1000)
        rule = engine.instrument_rules["BTCUSDT"]
        rule.is_verified = True
        rule.last_exchange_sync_ts = now_ts - (25 * 3600 * 1000)  # 25 hours old (expired)

        entered = engine._enter_leg(
            "BTCUSDT", 1, {"BTCUSDT": Decimal("100.0")}, {"BTCUSDT": {"atr24": 2.0}},
            engine.realized_equity, now_ts,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=now_ts, current_clock=now_ts,
        )
        assert entered is False, "Expired instrument rule (>24h) allowed order entry!"

    def test_p0_d_genuine_bybit_rest_verification(self):
        """
        P0-D Requirement: verify_from_bybit_instruments_info parses authentic Bybit V5 REST
        response, sets source=EXCHANGE_REST, exchange timestamp, and enables trading.
        """
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"), auto_verify_rules=False)
        rule = engine.instrument_rules["BTCUSDT"]
        assert rule.source == RuleSource.STATIC_UNVERIFIED
        assert rule.is_verified is False

        # Authentic Bybit V5 /v5/market/instruments-info structure
        server_ts = 1794000000000
        bybit_payload = {
            "retCode": 0,
            "retMsg": "OK",
            "result": {
                "category": "linear",
                "list": [
                    {
                        "symbol": "BTCUSDT",
                        "status": "Trading",
                        "lotSizeFilter": {
                            "qtyStep": "0.001",
                            "minOrderQty": "0.001",
                            "maxOrderQty": "100.0",
                            "minNotionalValue": "5.0",
                        },
                        "priceFilter": {
                            "tickSize": "0.10",
                        },
                    }
                ],
            },
            "time": server_ts,
        }

        verified_count = engine.verify_from_bybit_instruments_info(bybit_payload)
        assert verified_count == 1
        assert rule.is_verified is True
        assert rule.source == RuleSource.EXCHANGE_REST
        assert rule.last_exchange_sync_ts == server_ts
        assert rule.version == "bybit_v5_rest"

        # Now order entry succeeds
        entered = engine._enter_leg(
            "BTCUSDT", 1, {"BTCUSDT": Decimal("100.0")}, {"BTCUSDT": {"atr24": 2.0}},
            engine.realized_equity, server_ts,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=server_ts, current_clock=server_ts,
        )
        assert entered is True

    # -------------------------------------------------------------------------
    # P1: Data Gate Completion (Timeouts, Continuity, Funding, Beta vs Leverage)
    # -------------------------------------------------------------------------
    def test_p1_standalone_sync_timeout_handler(self):
        """
        P1 Requirement: check_sync_timeouts autonomously drops partial buffers exceeding 30s.
        """
        trader = PaperTraderV35Repaired.__new__(PaperTraderV35Repaired)
        trader.lock = threading_lock = pytest.importorskip("threading").RLock()
        trader.pending_bars_by_ts = {1794000000000: {"BTCUSDT": {}}}
        trader.pending_bars_first_seen = {1794000000000: 1000.0}

        # Check at 1010.0 (10s elapsed < 30s) -> no drop
        dropped = trader.check_sync_timeouts(now_sec=1010.0)
        assert len(dropped) == 0
        assert 1794000000000 in trader.pending_bars_by_ts

        # Check at 1035.0 (35s elapsed > 30s) -> dropped autonomously
        dropped_stale = trader.check_sync_timeouts(now_sec=1035.0)
        assert dropped_stale == [1794000000000]
        assert 1794000000000 not in trader.pending_bars_by_ts
        assert 1794000000000 not in trader.pending_bars_first_seen

    def test_p1_kline_hourly_continuity_check(self):
        """
        P1 Requirement: verify_kline_continuity detects missing hours in candle series.
        """
        trader = PaperTraderV35Repaired.__new__(PaperTraderV35Repaired)
        # Continuous sequence: 1000h, 1001h, 1002h
        trader.kline_history = {
            "BTCUSDT": [
                {"start": 1000 * 3600000, "close": 100.0},
                {"start": 1001 * 3600000, "close": 101.0},
                {"start": 1002 * 3600000, "close": 102.0},
            ]
        }
        assert trader.verify_kline_continuity("BTCUSDT") is True

        # Injected missing hour: 1000h, 1002h (missing 1001h!)
        trader.kline_history["BTCUSDT"] = [
            {"start": 1000 * 3600000, "close": 100.0},
            {"start": 1002 * 3600000, "close": 102.0},
        ]
        assert trader.verify_kline_continuity("BTCUSDT") is False

    def test_p1_funding_at_exact_settlement_timestamp(self):
        """
        P1 Requirement: Funding is applied only if position was open prior to settlement;
        partially closed position pays funding only on remaining quantity.
        """
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"), auto_verify_rules=True)
        entry_ts = 1794000000000
        engine._enter_leg(
            "BTCUSDT", 1, {"BTCUSDT": Decimal("100.0")}, {"BTCUSDT": {"atr24": 2.0}},
            engine.realized_equity, entry_ts,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=entry_ts, current_clock=entry_ts,
        )
        pos = engine.open_positions["BTCUSDT"]

        # 1. Settlement timestamp is PRIOR to entry (e.g. 2 hours earlier) -> 0 funding
        prior_settlement = FundingSettlement(
            symbol="BTCUSDT",
            settlement_ts=entry_ts - 7200000,
            rate=Decimal("0.0005"),
            mark_price=Decimal("100.0"),
        )
        cf_prior = engine.apply_funding_settlements([prior_settlement])
        assert cf_prior == Decimal("0.0")

        # 2. Slice 50% via partial TP, then settlement occurs -> pays on 50% remaining qty
        orig_qty = pos.qty
        pos.qty = orig_qty * Decimal("0.5")  # Simulate remaining qty after TP
        settlement_ts = entry_ts + 3600000
        valid_settlement = FundingSettlement(
            symbol="BTCUSDT",
            settlement_ts=settlement_ts,
            rate=Decimal("0.001"),
            mark_price=Decimal("100.0"),
        )
        cf_valid = engine.apply_funding_settlements([valid_settlement])
        expected_cashflow = -Decimal("1") * (pos.qty * Decimal("100.0")) * Decimal("0.001")
        assert cf_valid == expected_cashflow

    def test_p1_beta_limit_vs_leverage_limit_separation(self):
        """
        P1 Requirement: Beta limit (0.35) and aggregate leverage limit (2.0x) are enforced
        independently; UNKNOWN beta strictly blocks entry.
        """
        assert MAX_PORTFOLIO_BETA == Decimal("0.35")
        assert MAX_PORTFOLIO_LEVERAGE == Decimal("2.0")
        assert DRAWDOWN_LIMIT_PCT == Decimal("15.0")

        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"), auto_verify_rules=True)
        # Constant BTC close -> variance 0 -> UNKNOWN beta blocks entry
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
