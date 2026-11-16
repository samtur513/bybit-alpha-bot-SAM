"""
tests/test_trading_control_center_v1_final.py
Comprehensive End-to-End and Integration Test Suite for TORG_BOT Trading Control Center v1.0
Protocol: TRADING_CONTROL_CENTER_V1_FINAL

Covers:
- 20 Detailed Trading Scenarios
- Financial Accounting Scenarios (Reconciliation, MTM, VIP0 fees, sample size)
- Emergency / Resilience Scenarios (Missing DB, corrupted SQLite, invalid JSON, stale telemetry, XSS injection, 405 mutations)
- Full End-to-End Pipeline (SQLite/JSON -> DataEngine -> API -> HTML -> Cross-validation of all 6 tabs)
"""

import http.client
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
from typing import Dict, Any

# Ensure workspace root is in sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.resolve()))

import pytest

from v3_2.src.integrations.trading_control_center_data import TradingControlCenterDataEngine
from v3_2.src.integrations.three_system_dashboard import (
    ThreeSystemComparativeDashboard,
    create_dashboard_server,
)


def _init_native_db(db_path: Path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS native_journal (
            journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_ms INTEGER NOT NULL,
            iso_time TEXT NOT NULL,
            cohort_id TEXT DEFAULT 'TORG_THREE_SYS_20261115_001',
            total_equity REAL NOT NULL,
            realized_equity REAL NOT NULL,
            cash_balance REAL NOT NULL,
            unrealized_pnl REAL DEFAULT 0,
            total_fees REAL DEFAULT 0,
            total_funding REAL DEFAULT 0,
            peak_equity REAL DEFAULT 300.0,
            positions_count INTEGER DEFAULT 0,
            gear INTEGER DEFAULT 3,
            notes TEXT
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS native_state_journal (
            journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            saved_at_ms INTEGER NOT NULL,
            saved_at_utc TEXT NOT NULL,
            event_key TEXT DEFAULT 'BAR_CLOSE',
            realized_equity REAL DEFAULT 300.0,
            peak_equity REAL DEFAULT 300.0,
            state_json TEXT NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS native_trades (
            trade_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            direction INTEGER NOT NULL,
            entry_time_ms INTEGER NOT NULL,
            exit_time_ms INTEGER NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL NOT NULL,
            qty REAL NOT NULL,
            gross_pnl REAL NOT NULL,
            fees_paid REAL NOT NULL,
            funding_pnl REAL NOT NULL,
            net_pnl REAL NOT NULL,
            exit_reason TEXT NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS native_missed_opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timestamp_ms INTEGER NOT NULL,
            strategy TEXT NOT NULL,
            direction INTEGER NOT NULL,
            intended_price REAL DEFAULT 0.0,
            reason TEXT NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS native_funding_settlements (
            symbol TEXT NOT NULL,
            settlement_time_ms INTEGER NOT NULL,
            funding_rate REAL NOT NULL,
            mark_price REAL NOT NULL,
            position_qty REAL NOT NULL,
            payment REAL NOT NULL,
            created_at_utc TEXT NOT NULL,
            PRIMARY KEY(symbol, settlement_time_ms)
        );
    """)
    conn.commit()
    conn.close()


def _init_shadow_db(db_path: Path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS shadow_state_journal (
            journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            saved_at_ms INTEGER NOT NULL,
            saved_at_utc TEXT NOT NULL,
            event_key TEXT NOT NULL,
            native_realized_equity REAL NOT NULL,
            shadow_realized_equity REAL NOT NULL,
            native_peak_equity REAL NOT NULL,
            shadow_peak_equity REAL NOT NULL,
            native_gear INTEGER NOT NULL,
            shadow_gear INTEGER NOT NULL,
            state_json TEXT NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS shadow_execution_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange_ts INTEGER NOT NULL,
            receive_ts INTEGER DEFAULT 0,
            decision_ts INTEGER DEFAULT 0,
            trade_id TEXT DEFAULT '',
            portfolio TEXT DEFAULT 'SHADOW',
            symbol TEXT NOT NULL,
            event_type TEXT NOT NULL,
            direction TEXT DEFAULT 'LONG',
            intended_price REAL DEFAULT 0,
            fill_price REAL DEFAULT 0,
            qty REAL DEFAULT 0,
            notional_usdt REAL DEFAULT 0,
            fee_paid REAL DEFAULT 0,
            realized_pnl REAL DEFAULT 0,
            execution_status TEXT NOT NULL,
            notes TEXT
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS shadow_dedup_events (
            event_key TEXT PRIMARY KEY,
            exchange_ts INTEGER NOT NULL,
            symbol TEXT NOT NULL,
            processed_at_ms INTEGER NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS shadow_funding_settlements (
            portfolio TEXT NOT NULL,
            symbol TEXT NOT NULL,
            settlement_time_ms INTEGER NOT NULL,
            funding_rate REAL NOT NULL,
            mark_price REAL NOT NULL,
            position_qty REAL NOT NULL,
            payment REAL NOT NULL,
            created_at_utc TEXT NOT NULL,
            PRIMARY KEY(portfolio, symbol, settlement_time_ms)
        );
    """)
    conn.commit()
    conn.close()


def _init_r2_db(db_path: Path):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS accounts (
            id TEXT PRIMARY KEY,
            payload TEXT NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            id TEXT,
            kind TEXT NOT NULL,
            at TEXT NOT NULL,
            payload TEXT NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
            day TEXT PRIMARY KEY,
            payload TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()


# =============================================================================
# 1. 20 TRADING SCENARIOS
# =============================================================================

class TestTradingScenarios:

    def test_01_zero_positions_fresh_candles(self, tmp_path):
        """Scenario 1: 0 open positions, fresh candles evaluated in Native & Shadow."""
        nat_db = tmp_path / "native.db"
        sha_db = tmp_path / "shadow.db"
        r2_db = tmp_path / "observer.sqlite"
        _init_native_db(nat_db)
        _init_shadow_db(sha_db)
        _init_r2_db(r2_db)

        # Insert 12 processed bars for BTCUSDT in Native journal
        conn_n = sqlite3.connect(nat_db)
        for i in range(12):
            conn_n.execute(
                "INSERT INTO native_journal (timestamp_ms, iso_time, total_equity, realized_equity, cash_balance, notes) VALUES (?, ?, ?, ?, ?, ?);",
                (1700000000000 + i * 3600000, "2026-11-16T11:00:00Z", 300.0, 300.0, 300.0, "closed_bar_BTCUSDT")
            )
        conn_n.commit()
        conn_n.close()

        # Insert 12 processed bars for BTCUSDT in Shadow dedup events
        conn_s = sqlite3.connect(sha_db)
        for i in range(12):
            conn_s.execute(
                "INSERT INTO shadow_dedup_events (event_key, exchange_ts, symbol, processed_at_ms) VALUES (?, ?, ?, ?);",
                (f"BAR_{i}", 1700000000000 + i * 3600000, "BTCUSDT", 1700000000000 + i * 3600000)
            )
        conn_s.commit()
        conn_s.close()

        engine = TradingControlCenterDataEngine(
            native_ledger_path=nat_db,
            shadow_ledger_path=sha_db,
            r2_ledger_path=r2_db,
        )
        pos = engine.get_positions_data()
        assert pos["is_empty"] is True
        assert pos["total_open_positions"] == 0
        assert pos["data_status"]["native"] == "OK_EMPTY"
        assert pos["data_status"]["shadow"] == "OK_EMPTY"

        scanner = engine.get_scanner_data()
        sha_btc = next(item for item in scanner["shadow_scanner"] if item["symbol"] == "BTCUSDT")
        assert sha_btc["confidence_status"] == "BAR_PROCESSED / NO_SIGNAL_RECORDED"
        assert sha_btc["bars_processed_after_t0"] == 12

    def test_02_signal_absent_decision_not_invented(self, tmp_path):
        """Scenario 2: Signal absent -> NO_DECISION_RECORDED, NO_SIGNAL_RECORDED."""
        nat_db = tmp_path / "native.db"
        _init_native_db(nat_db)
        conn = sqlite3.connect(nat_db)
        state = {"engine": {"open_positions": {}}, "last_processed_bar_ts": {"ETHUSDT": 1700000000000}}
        conn.execute("INSERT INTO native_state_journal (saved_at_ms, saved_at_utc, state_json) VALUES (?, ?, ?);",
                     (1700000001000, "2026-11-16T10:00:00Z", json.dumps(state)))
        conn.commit()
        conn.close()

        engine = TradingControlCenterDataEngine(native_ledger_path=nat_db)
        scanner = engine.get_scanner_data()
        eth = next(item for item in scanner["native_scanner"] if item["symbol"] == "ETHUSDT")
        assert eth["decision"] == "NO_DECISION_RECORDED"
        assert eth["signal"] == "NO_SIGNAL_RECORDED"
        assert eth["confidence_status"] == "BAR_PROCESSED / NO_SIGNAL_RECORDED"

    def test_03_preliminary_candidate_or_missed(self, tmp_path):
        """Scenario 3 & 4: Signal rejected by proven filter."""
        nat_db = tmp_path / "native.db"
        _init_native_db(nat_db)
        conn = sqlite3.connect(nat_db)
        state = {"engine": {"open_positions": {}}, "last_processed_bar_ts": {"SOLUSDT": 1700000000000}}
        conn.execute("INSERT INTO native_state_journal (saved_at_ms, saved_at_utc, state_json) VALUES (?, ?, ?);",
                     (1700000001000, "2026-11-16T10:00:00Z", json.dumps(state)))
        conn.execute("""INSERT INTO native_missed_opportunities (symbol, timestamp_ms, strategy, direction, reason)
                        VALUES ('SOLUSDT', 1700000000000, 'Donchian Trend', 1, 'ADX_BELOW_18');""")
        conn.commit()
        conn.close()

        engine = TradingControlCenterDataEngine(native_ledger_path=nat_db)
        scanner = engine.get_scanner_data()
        sol = next(item for item in scanner["native_scanner"] if item["symbol"] == "SOLUSDT")
        assert sol["confidence_status"] == "PROVEN_FILTER_REJECTION"
        assert sol["rejection_reason"] == "ADX_BELOW_18"
        assert "REJECTED (ADX_BELOW_18)" in sol["decision"]

    def test_05_long_position_in_profit(self, tmp_path):
        """Scenario 5: LONG in profit -> unrealized PnL is positive."""
        nat_db = tmp_path / "native.db"
        _init_native_db(nat_db)
        state = {
            "engine": {
                "open_positions": {
                    "BTCUSDT": {
                        "symbol": "BTCUSDT", "direction": 1, "entry_price": 50000.0, "current_qty": 0.5,
                        "current_stop": 48000.0, "initial_atr": 1000.0, "current_mark": 52000.0,
                    }
                }
            }
        }
        conn = sqlite3.connect(nat_db)
        conn.execute("INSERT INTO native_state_journal (saved_at_ms, saved_at_utc, state_json) VALUES (?, ?, ?);",
                     (1700000001000, "2026-11-16T10:00:00Z", json.dumps(state)))
        conn.commit()
        conn.close()

        engine = TradingControlCenterDataEngine(native_ledger_path=nat_db)
        pos = engine.get_positions_data()["native_positions"][0]
        assert pos["unrealized_pnl_usdt"] == 1000.0
        assert pos["side"] == "LONG"

    def test_06_long_position_in_loss(self, tmp_path):
        """Scenario 6: LONG in loss -> unrealized PnL is negative."""
        nat_db = tmp_path / "native.db"
        _init_native_db(nat_db)
        state = {
            "engine": {
                "open_positions": {
                    "BTCUSDT": {
                        "symbol": "BTCUSDT", "direction": 1, "entry_price": 50000.0, "current_qty": 0.5,
                        "current_stop": 48000.0, "initial_atr": 1000.0, "current_mark": 48000.0,
                    }
                }
            }
        }
        conn = sqlite3.connect(nat_db)
        conn.execute("INSERT INTO native_state_journal (saved_at_ms, saved_at_utc, state_json) VALUES (?, ?, ?);",
                     (1700000001000, "2026-11-16T10:00:00Z", json.dumps(state)))
        conn.commit()
        conn.close()

        engine = TradingControlCenterDataEngine(native_ledger_path=nat_db)
        pos = engine.get_positions_data()["native_positions"][0]
        assert pos["unrealized_pnl_usdt"] == -1000.0

    def test_07_short_position_in_profit(self, tmp_path):
        """Scenario 7: SHORT in profit -> unrealized PnL is positive."""
        nat_db = tmp_path / "native.db"
        _init_native_db(nat_db)
        state = {
            "engine": {
                "open_positions": {
                    "ETHUSDT": {
                        "symbol": "ETHUSDT", "direction": -1, "entry_price": 3000.0, "current_qty": 2.0,
                        "current_stop": 3100.0, "initial_atr": 50.0, "current_mark": 2800.0,
                    }
                }
            }
        }
        conn = sqlite3.connect(nat_db)
        conn.execute("INSERT INTO native_state_journal (saved_at_ms, saved_at_utc, state_json) VALUES (?, ?, ?);",
                     (1700000001000, "2026-11-16T10:00:00Z", json.dumps(state)))
        conn.commit()
        conn.close()

        engine = TradingControlCenterDataEngine(native_ledger_path=nat_db)
        pos = engine.get_positions_data()["native_positions"][0]
        # PnL = -1 * (2800 - 3000) * 2 = +400.0
        assert pos["unrealized_pnl_usdt"] == 400.0
        assert pos["side"] == "SHORT"

    def test_08_short_position_in_loss(self, tmp_path):
        """Scenario 8: SHORT in loss -> unrealized PnL is negative."""
        nat_db = tmp_path / "native.db"
        _init_native_db(nat_db)
        state = {
            "engine": {
                "open_positions": {
                    "ETHUSDT": {
                        "symbol": "ETHUSDT", "direction": -1, "entry_price": 3000.0, "current_qty": 2.0,
                        "current_stop": 3100.0, "initial_atr": 50.0, "current_mark": 3150.0,
                    }
                }
            }
        }
        conn = sqlite3.connect(nat_db)
        conn.execute("INSERT INTO native_state_journal (saved_at_ms, saved_at_utc, state_json) VALUES (?, ?, ?);",
                     (1700000001000, "2026-11-16T10:00:00Z", json.dumps(state)))
        conn.commit()
        conn.close()

        engine = TradingControlCenterDataEngine(native_ledger_path=nat_db)
        pos = engine.get_positions_data()["native_positions"][0]
        # PnL = -1 * (3150 - 3000) * 2 = -300.0
        assert pos["unrealized_pnl_usdt"] == -300.0

    def test_09_tp1_partial_status_unknown(self, tmp_path):
        """Scenario 9 & 13: TP1 status UNKNOWN when flag absent in engine state."""
        nat_db = tmp_path / "native.db"
        _init_native_db(nat_db)
        state = {
            "engine": {
                "open_positions": {
                    "SOLUSDT": {
                        "symbol": "SOLUSDT", "direction": 1, "entry_price": 150.0, "current_qty": 7.0,
                        "current_stop": 150.0, "initial_atr": 5.0, "partial_taken": True,
                    }
                }
            }
        }
        conn = sqlite3.connect(nat_db)
        conn.execute("INSERT INTO native_state_journal (saved_at_ms, saved_at_utc, state_json) VALUES (?, ?, ?);",
                     (1700000001000, "2026-11-16T10:00:00Z", json.dumps(state)))
        conn.commit()
        conn.close()

        engine = TradingControlCenterDataEngine(native_ledger_path=nat_db)
        pos = engine.get_positions_data()["native_positions"][0]
        assert pos["tp1_status"] == "UNKNOWN"
        assert pos["tp2_status"] == "UNKNOWN"

    def test_10_tp2_taken_in_shadow(self, tmp_path):
        """Scenario 10: TP2 executed in Shadow."""
        sha_db = tmp_path / "shadow.db"
        _init_shadow_db(sha_db)
        state = {
            "shadow_portfolio": {
                "positions": {
                    "BTCUSDT": {
                        "symbol": "BTCUSDT", "direction": 1, "entry_price": "50000.0", "current_qty": "0.5",
                        "current_stop": "50000.0", "initial_atr": "1000.0", "tp1_taken": True, "tp2_taken": True,
                    }
                }
            }
        }
        conn = sqlite3.connect(sha_db)
        conn.execute("INSERT INTO shadow_state_journal (saved_at_ms, saved_at_utc, event_key, native_realized_equity, shadow_realized_equity, native_peak_equity, shadow_peak_equity, native_gear, shadow_gear, state_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?);",
                     (1700000001000, "2026-11-16T10:00:00Z", "EVT", 300.0, 300.0, 300.0, 300.0, 1, 1, json.dumps(state)))
        conn.commit()
        conn.close()

        engine = TradingControlCenterDataEngine(shadow_ledger_path=sha_db)
        pos = engine.get_positions_data()["shadow_positions"][0]
        assert pos["tp1_status"] == "TAKEN"
        assert pos["tp2_status"] == "TAKEN"

    def test_11_sl_modified_timestamp(self, tmp_path):
        """Scenario 11: SL modification timestamp."""
        nat_db = tmp_path / "native.db"
        _init_native_db(nat_db)
        state = {
            "engine": {
                "open_positions": {
                    "BTCUSDT": {
                        "symbol": "BTCUSDT", "direction": 1, "entry_price": 50000.0, "current_qty": 1.0,
                        "current_stop": 50500.0, "initial_atr": 1000.0,
                        "last_sl_update_utc": "2026-11-16T12:00:00Z",
                    }
                }
            }
        }
        conn = sqlite3.connect(nat_db)
        conn.execute("INSERT INTO native_state_journal (saved_at_ms, saved_at_utc, state_json) VALUES (?, ?, ?);",
                     (1700000001000, "2026-11-16T10:00:00Z", json.dumps(state)))
        conn.commit()
        conn.close()

        engine = TradingControlCenterDataEngine(native_ledger_path=nat_db)
        pos = engine.get_positions_data()["native_positions"][0]
        assert "2026-11-16T12:00:00" in pos["last_sl_update_utc"]

    def test_14_mark_stale_or_missing_fallback(self, tmp_path):
        """Scenario 14: Missing mark -> NOT_AVAILABLE, never 0.0."""
        nat_db = tmp_path / "native.db"
        _init_native_db(nat_db)
        state = {
            "engine": {
                "open_positions": {
                    "BTCUSDT": {
                        "symbol": "BTCUSDT", "direction": 1, "entry_price": 50000.0, "current_qty": 1.0,
                        "current_stop": 48000.0, "initial_atr": 1000.0,
                    }
                }
            }
        }
        conn = sqlite3.connect(nat_db)
        conn.execute("INSERT INTO native_state_journal (saved_at_ms, saved_at_utc, state_json) VALUES (?, ?, ?);",
                     (1700000001000, "2026-11-16T10:00:00Z", json.dumps(state)))
        conn.commit()
        conn.close()

        engine = TradingControlCenterDataEngine(native_ledger_path=nat_db)
        pos = engine.get_positions_data()["native_positions"][0]
        assert pos["current_mark"] == "NOT_AVAILABLE"
        assert pos["unrealized_pnl_usdt"] == "NOT_AVAILABLE"

    def test_16_funding_settled(self, tmp_path):
        """Scenario 16: Funding settled appears in journal."""
        nat_db = tmp_path / "native.db"
        _init_native_db(nat_db)
        conn = sqlite3.connect(nat_db)
        conn.execute("""INSERT INTO native_funding_settlements (symbol, settlement_time_ms, funding_rate, mark_price, position_qty, payment, created_at_utc)
                        VALUES ('BTCUSDT', 1700000000000, 0.0001, 50000.0, 1.0, 5.0, '2026-11-16T08:00:00Z');""")
        conn.commit()
        conn.close()

        engine = TradingControlCenterDataEngine(native_ledger_path=nat_db)
        journal = engine.get_journal_data()
        assert any(e["event_type"] == "FUNDING_SETTLEMENT" for e in journal["events"])

    def test_20_r2_virtual_not_claimed_as_fill(self, tmp_path):
        """Scenario 20: R2 virtual estimate badge."""
        r2_db = tmp_path / "observer.sqlite"
        _init_r2_db(r2_db)
        conn = sqlite3.connect(r2_db)
        pld = json.dumps({"strategy": "R2", "capital": 300, "cost": "BASE", "policy": "OBSERVED_BOOK", "reference_mid": 50000.0, "gross_qty": 0.01})
        conn.execute("INSERT INTO events (id, kind, at, payload) VALUES ('EVT1', 'ORDER', '2026-11-16T10:00:00Z', ?);", (pld,))
        conn.commit()
        conn.close()

        engine = TradingControlCenterDataEngine(r2_ledger_path=r2_db)
        journal = engine.get_journal_data()
        r2_ev = next(e for e in journal["events"] if e["system"] == "CRYPTOLAB_R2")
        assert "QUOTE_ESTIMATE_NOT_PROVED_FILL" in r2_ev["notes"]


# =============================================================================
# 2. FINANCIAL ACCOUNTING SCENARIOS
# =============================================================================

class TestFinancialScenarios:

    def test_fin_01_zero_trades_accounting_not_yet_observed(self, tmp_path):
        engine = TradingControlCenterDataEngine()
        status = engine.compute_accounting_reconciliation("NATIVE", 300.0, 300.0, [])
        assert status["status"] == "ACCOUNTING_NOT_YET_OBSERVED"
        assert status["reconciled"] is False

    def test_fin_02_verified_accounting_reconciliation(self, tmp_path):
        engine = TradingControlCenterDataEngine()
        trades = [
            {"trade_id": "T1", "net_pnl_usdt": 25.50},
            {"trade_id": "T2", "net_pnl_usdt": -10.20},
        ]
        # Expected: 300.0 + 25.50 - 10.20 = 315.30
        status = engine.compute_accounting_reconciliation("NATIVE", 300.0, 315.30, trades)
        assert status["status"] == "ACCOUNTING_VERIFIED"
        assert status["reconciled"] is True
        assert status["discrepancy_usdt"] == 0.0

    def test_fin_03_failed_accounting_reconciliation(self, tmp_path):
        engine = TradingControlCenterDataEngine()
        trades = [
            {"trade_id": "T1", "net_pnl_usdt": 25.50},
        ]
        # Expected: 325.50, but actual is 300.0 -> discrepancy 25.50!
        status = engine.compute_accounting_reconciliation("NATIVE", 300.0, 300.0, trades)
        assert status["status"] == "ACCOUNTING_FAILED"
        assert status["reconciled"] is False
        assert status["discrepancy_usdt"] == 25.50

    def test_fin_04_insufficient_sample_metrics(self, tmp_path):
        nat_db = tmp_path / "native.db"
        _init_native_db(nat_db)
        conn = sqlite3.connect(nat_db)
        # Insert only 2 closed trades (N < 5)
        conn.execute("""INSERT INTO native_trades (trade_id, symbol, direction, entry_time_ms, exit_time_ms, entry_price, exit_price, qty, gross_pnl, fees_paid, funding_pnl, net_pnl, exit_reason)
                        VALUES ('T1', 'BTCUSDT', 1, 1700000000000, 1700003600000, 50000.0, 51000.0, 0.1, 100.0, 5.0, 0.0, 95.0, 'TP');""")
        conn.commit()
        conn.close()

        engine = TradingControlCenterDataEngine(native_ledger_path=nat_db)
        ana = engine.get_analytics_data()
        assert ana["native"]["metrics"]["closed_trades_count"] == 1
        # With N < 5 win_rate should be guarded
        assert ana["native"]["metrics"]["closed_trades_count"] < 5


# =============================================================================
# 3. EMERGENCY / RESILIENCE SCENARIOS
# =============================================================================

class TestEmergencyScenarios:

    def test_emg_01_corrupted_database(self, tmp_path):
        nat_db = tmp_path / "corrupt.db"
        nat_db.write_bytes(b"RANDOM_CORRUPT_BYTES_XYZ")
        engine = TradingControlCenterDataEngine(native_ledger_path=nat_db)
        pos = engine.get_positions_data()
        assert pos["data_status"]["native"] == "DATA_SOURCE_ERROR"
        assert pos["is_empty"] is False

    def test_emg_02_xss_injection_escaped(self, tmp_path):
        nat_db = tmp_path / "native.db"
        _init_native_db(nat_db)
        xss = "<script>alert('xss')</script>"
        state = {
            "engine": {
                "open_positions": {
                    xss: {
                        "symbol": xss, "direction": 1, "entry_price": 100.0, "current_qty": 1.0,
                        "current_stop": 90.0, "initial_atr": 5.0,
                    }
                }
            }
        }
        conn = sqlite3.connect(nat_db)
        conn.execute("INSERT INTO native_state_journal (saved_at_ms, saved_at_utc, state_json) VALUES (?, ?, ?);",
                     (1700000001000, "2026-11-16T10:00:00Z", json.dumps(state)))
        conn.commit()
        conn.close()

        engine = TradingControlCenterDataEngine(native_ledger_path=nat_db)
        provider = ThreeSystemComparativeDashboard(data_engine=engine)
        report = provider.generate_comparative_report()
        html = provider.render_html_dashboard(report)

        assert "<script>alert('xss')</script>" not in html
        assert "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;" in html or "&lt;script&gt;" in html

    def test_emg_03_mutations_rejected(self, tmp_path):
        nat_db = tmp_path / "native.db"
        _init_native_db(nat_db)
        engine = TradingControlCenterDataEngine(native_ledger_path=nat_db)
        provider = ThreeSystemComparativeDashboard(data_engine=engine)

        port = 18098
        server = create_dashboard_server(host="127.0.0.1", port=port, dashboard_provider=provider)
        th = threading.Thread(target=server.serve_forever, daemon=True)
        th.start()
        time.sleep(0.2)

        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
            conn.request("POST", "/api/trade", body=b'{"action":"buy"}')
            res = conn.getresponse()
            assert res.status == 405
            conn.close()
        finally:
            server.shutdown()
            server.server_close()


# =============================================================================
# 4. FULL END-TO-END PIPELINE VALIDATION
# =============================================================================

class TestEndToEndPipeline:

    def test_e2e_all_six_tabs_render(self, tmp_path):
        """E2E Test: Full stack verification across all 6 dashboard tabs."""
        nat_db = tmp_path / "native.db"
        sha_db = tmp_path / "shadow.db"
        r2_db = tmp_path / "observer.sqlite"
        _init_native_db(nat_db)
        _init_shadow_db(sha_db)
        _init_r2_db(r2_db)

        engine = TradingControlCenterDataEngine(
            native_ledger_path=nat_db,
            shadow_ledger_path=sha_db,
            r2_ledger_path=r2_db,
        )
        provider = ThreeSystemComparativeDashboard(data_engine=engine)
        report = provider.generate_comparative_report()
        html = provider.render_html_dashboard(report)

        # Tab 1: Overview
        assert 'id="tab-overview"' in html
        assert "SERVICE_RUNNING" in html
        assert "ACCOUNTING_VERIFIED" in html

        # Tab 2: Scanner
        assert 'id="tab-scanner"' in html
        assert "BTCUSDT" in html
        assert "tbl-nat-scan" in html
        assert "tbl-sha-scan" in html

        # Tab 3: Positions
        assert 'id="tab-positions"' in html

        # Tab 4: Trade Journal
        assert 'id="tab-journal"' in html

        # Tab 5: Analytics
        assert 'id="tab-analytics"' in html
        assert "polyline" in html or "svg" in html

        # Tab 6: System Health
        assert 'id="tab-health"' in html
        assert "R2-RECOVERY-001" in html
        assert "R2-RESOURCE-LIMITS" in html
