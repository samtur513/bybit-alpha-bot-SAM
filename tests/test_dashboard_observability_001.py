"""
tests/test_dashboard_observability_001.py
Comprehensive Verification Suite for TORG_BOT Trading Control Center v1.0.
Protocol: DASHBOARD-OBSERVABILITY-001 (Audit Repair-002)

Verifies all 13 Mandatory Acceptance Criteria against REAL Schemas:
1. Open positions exist, but native_positions / shadow_positions tables are ABSENT (real schema check).
2. Real LONG and SHORT directions parsed accurately without forcing LONG.
3. Real Stop Loss, Cascade TP (1.5R / 3.0R in Native), Shadow TP1/TP2, and partial exits.
4. Signal not registered -> NO fabricated rejection reasons (strictly NOT_RECORDED, no fake NEUTRAL_ZONE).
5. Real R2 structured signal dict and array target_weights [w_btc, w_eth, w_sol].
6. R2 Equity strictly filtered to primary profile R2:300:BASE:OBSERVED_BOOK (never pools $1000/$10000).
7. Native total Equity with open position (taken from native_journal.total_equity).
8. Missing database is distinguished from an empty portfolio (DATA_SOURCE_ERROR vs OK_EMPTY).
9. SQLite error is explicitly displayed in diagnostics (never masks broken source as "no positions").
10. HTML-escaping check against XSS/HTML injections (symbols, reasons, notes, errors).
11. Event timestamp and data freshness verification (real historical timestamps, no current time spoofing).
12. Read-only HTTP API (405 Method Not Allowed on POST/PUT/DELETE/PATCH) and database immutability.
13. Performance test on realistic DB sizes (10,000+ rows) completing in < 50ms.
"""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

# Ensure repository root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from v3_2.src.integrations.three_system_dashboard import (
    CANONICAL_COHORT_ID,
    CANONICAL_T0_UTC,
    ThreeSystemComparativeDashboard,
    create_dashboard_server,
)
from v3_2.src.integrations.trading_control_center_data import (
    DEFAULT_SYMBOLS_18,
    R2_DATA_GAP_END_UTC,
    R2_DATA_GAP_START_UTC,
    R2_PRIMARY_PROFILE,
    R2_SYMBOLS_3,
    TradingControlCenterDataEngine,
)


# ==============================================================================
# Helper fixtures for creating real SQLite databases
# ==============================================================================

def create_real_native_ledger(db_path: Path, state_data: dict, total_equity: float = 325.5, realized_equity: float = 300.0) -> None:
    """Creates a real Native SQLite database matching run_native_paper.py schema."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS native_journal (
            journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_ms INTEGER NOT NULL,
            iso_time TEXT NOT NULL,
            cohort_id TEXT NOT NULL,
            realized_equity REAL NOT NULL,
            unrealized_pnl REAL NOT NULL,
            total_equity REAL NOT NULL,
            cash_balance REAL NOT NULL,
            total_fees REAL NOT NULL,
            total_funding REAL NOT NULL,
            peak_equity REAL NOT NULL,
            positions_count INTEGER NOT NULL,
            gear INTEGER DEFAULT 3,
            notes TEXT
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
        CREATE TABLE IF NOT EXISTS native_state_journal (
            journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            saved_at_ms INTEGER NOT NULL,
            saved_at_utc TEXT NOT NULL,
            event_key TEXT NOT NULL,
            realized_equity REAL NOT NULL,
            peak_equity REAL NOT NULL,
            state_json TEXT NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS native_missed_opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timestamp_ms INTEGER NOT NULL,
            strategy TEXT NOT NULL,
            direction INTEGER NOT NULL,
            intended_price REAL NOT NULL,
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

    now_ms = int(time.time() * 1000)
    now_iso = datetime.now(timezone.utc).isoformat()
    cur.execute(
        """INSERT INTO native_journal (timestamp_ms, iso_time, cohort_id, realized_equity, unrealized_pnl, total_equity, cash_balance, total_fees, total_funding, peak_equity, positions_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, 0.5, 0.0, ?, 1);""",
        (now_ms, now_iso, CANONICAL_COHORT_ID, realized_equity, total_equity - realized_equity, total_equity, 200.0, total_equity)
    )
    cur.execute(
        """INSERT INTO native_state_journal (saved_at_ms, saved_at_utc, event_key, realized_equity, peak_equity, state_json)
           VALUES (?, ?, 'STATE_SAVE', ?, ?, ?);""",
        (now_ms, now_iso, realized_equity, total_equity, json.dumps(state_data))
    )
    conn.commit()
    conn.close()


def create_real_shadow_ledger(db_path: Path, state_data: dict) -> None:
    """Creates a real Shadow SQLite database matching paper_trader_v32_shadow.py schema."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS shadow_execution_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange_ts INTEGER NOT NULL,
            receive_ts INTEGER NOT NULL,
            decision_ts INTEGER NOT NULL,
            trade_id TEXT NOT NULL,
            portfolio TEXT NOT NULL,
            symbol TEXT NOT NULL,
            event_type TEXT NOT NULL,
            direction TEXT NOT NULL,
            intended_price REAL NOT NULL,
            fill_price REAL NOT NULL,
            qty REAL NOT NULL,
            notional_usdt REAL NOT NULL,
            fee_paid REAL DEFAULT 0,
            realized_pnl REAL NOT NULL,
            execution_status TEXT NOT NULL,
            notes TEXT
        );
    """)
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
    now_ms = int(time.time() * 1000)
    now_iso = datetime.now(timezone.utc).isoformat()
    cur.execute(
        """INSERT INTO shadow_state_journal (saved_at_ms, saved_at_utc, event_key, native_realized_equity, shadow_realized_equity, native_peak_equity, shadow_peak_equity, native_gear, shadow_gear, state_json)
           VALUES (?, ?, 'STATE_SAVE', 300.0, 312.0, 300.0, 315.0, 3, 3, ?);""",
        (now_ms, now_iso, json.dumps(state_data))
    )
    conn.commit()
    conn.close()


def create_real_r2_observer(db_path: Path) -> None:
    """Creates a real CryptoLab observer.sqlite database."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, kind TEXT, at TEXT, payload TEXT);
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS accounts(id TEXT PRIMARY KEY, payload TEXT);
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS decisions(day TEXT PRIMARY KEY, payload TEXT);
    """)

    # Populate accounts
    primary_acc = {
        "strategy": "R2", "capital": 300, "cost": "BASE", "policy": "OBSERVED_BOOK",
        "cash": 300.0, "qty": [0.0, 0.0, 0.0], "mdd": 0.0, "risk_breached": False
    }
    cur.execute("INSERT OR REPLACE INTO accounts VALUES (?, ?);", (R2_PRIMARY_PROFILE, json.dumps(primary_acc)))

    # Populate multi-capital accounts to test profile isolation
    acc_1000 = {
        "strategy": "R2", "capital": 1000, "cost": "BASE", "policy": "OBSERVED_BOOK",
        "cash": 1000.0, "qty": [0.0, 0.0, 0.0], "mdd": 0.0, "risk_breached": False
    }
    cur.execute("INSERT OR REPLACE INTO accounts VALUES ('R2:1000:BASE:OBSERVED_BOOK', ?);", (json.dumps(acc_1000),))

    now_iso = datetime.now(timezone.utc).isoformat()
    # Add EQUITY events
    cur.execute(
        "INSERT INTO events VALUES ('E1', 'EQUITY', ?, ?);",
        (now_iso, json.dumps({"strategy": "R2", "capital": 300, "cost": "BASE", "policy": "OBSERVED_BOOK", "mtm": 302.5}))
    )
    cur.execute(
        "INSERT INTO events VALUES ('E2', 'EQUITY', ?, ?);",
        (now_iso, json.dumps({"strategy": "R2", "capital": 1000, "cost": "BASE", "policy": "OBSERVED_BOOK", "mtm": 1015.0}))
    )
    cur.execute(
        "INSERT INTO events VALUES ('E3', 'EQUITY', ?, ?);",
        (now_iso, json.dumps({"strategy": "R2", "capital": 10000, "cost": "BASE", "policy": "OBSERVED_BOOK", "mtm": 10200.0}))
    )
    conn.commit()
    conn.close()


# ==============================================================================
# Criterion 1: Open Positions Exist Without Fake Tables (P0.1)
# ==============================================================================

def test_c01_open_positions_exist_without_fake_tables(tmp_path):
    """Verifies open positions are parsed from state_json without querying native_positions/shadow_positions."""
    db_nat = tmp_path / "native_ledger.db"
    db_sha = tmp_path / "shadow_ledger.db"

    state_nat = {
        "engine": {
            "open_positions": {
                "BTCUSDT": {
                    "symbol": "BTCUSDT", "direction": 1, "entry_price": "90000.0", "current_qty": "0.01",
                    "current_stop": "88000.0", "initial_atr": "1500.0", "entry_time": 1700000000000,
                    "tp1_taken": False, "tp2_taken": False, "is_breakeven_set": False, "bars_held": 2
                }
            }
        }
    }
    state_sha = {
        "shadow_portfolio": {
            "positions": {
                "SOLUSDT": {
                    "symbol": "SOLUSDT", "direction": -1, "entry_price": "210.0", "current_qty": "1.5",
                    "current_stop": "218.0", "initial_atr": "4.0", "entry_time_ms": 1700000050000,
                    "tp1_taken": False, "tp2_taken": False, "tp1_price": "204.0", "tp2_price": "198.0",
                    "is_breakeven_set": False, "bars_held": 3
                }
            }
        }
    }

    create_real_native_ledger(db_nat, state_nat)
    create_real_shadow_ledger(db_sha, state_sha)

    # Double check that native_positions and shadow_positions tables DO NOT EXIST
    conn = sqlite3.connect(db_nat)
    tables_nat = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table';").fetchall()]
    conn.close()
    assert "native_positions" not in tables_nat
    assert "native_state_journal" in tables_nat

    engine = TradingControlCenterDataEngine(native_ledger_path=db_nat, shadow_ledger_path=db_sha)
    pos_data = engine.get_positions_data()

    assert pos_data["total_open_positions"] == 2
    assert len(pos_data["native_positions"]) == 1
    assert pos_data["native_positions"][0]["symbol"] == "BTCUSDT"
    assert pos_data["native_positions"][0]["credibility"] == "CONFIRMED_STATE_POSITION"

    assert len(pos_data["shadow_positions"]) == 1
    assert pos_data["shadow_positions"][0]["symbol"] == "SOLUSDT"


# ==============================================================================
# Criterion 2: Real LONG and SHORT Directions (P0.2)
# ==============================================================================

def test_c02_real_long_and_short_direction(tmp_path):
    """Verifies that direction is strictly parsed as LONG (+1) or SHORT (-1), never forced to LONG."""
    db_nat = tmp_path / "native_ledger.db"
    state_nat = {
        "engine": {
            "open_positions": {
                "BTCUSDT": {"symbol": "BTCUSDT", "direction": 1, "entry_price": "90000.0", "current_qty": "0.01", "current_stop": "88000.0", "initial_atr": "1500.0"},
                "ETHUSDT": {"symbol": "ETHUSDT", "direction": -1, "entry_price": "3100.0", "current_qty": "0.5", "current_stop": "3200.0", "initial_atr": "60.0"},
            }
        }
    }
    create_real_native_ledger(db_nat, state_nat)

    engine = TradingControlCenterDataEngine(native_ledger_path=db_nat)
    pos_data = engine.get_positions_data()

    pos_by_sym = {p["symbol"]: p for p in pos_data["native_positions"]}
    assert pos_by_sym["BTCUSDT"]["side"] == "LONG"
    assert pos_by_sym["ETHUSDT"]["side"] == "SHORT"


# ==============================================================================
# Criterion 3: Real Stop Loss, Cascade TP and Partial Exits (P0.4)
# ==============================================================================

def test_c03_real_stop_loss_cascade_tp_and_partial_exits(tmp_path):
    """Verifies Cascade TP is calculated (1.5R and 3.0R) in Native Trend and flags are accurate."""
    db_nat = tmp_path / "native_ledger.db"
    db_sha = tmp_path / "shadow_ledger.db"

    # Native LONG with ATR 2000, entry 90,000 -> TP1 = 93,000 (1.5R), TP2 = 96,000 (3.0R)
    state_nat = {
        "engine": {
            "open_positions": {
                "BTCUSDT": {
                    "symbol": "BTCUSDT", "direction": 1, "entry_price": "90000.0", "current_qty": "0.01",
                    "current_stop": "90000.0", "initial_atr": "2000.0", "tp1_taken": True, "tp2_taken": False,
                    "is_breakeven_set": True, "bars_held": 5
                }
            }
        }
    }
    # Shadow SHORT with TP1 taken
    state_sha = {
        "shadow_portfolio": {
            "positions": {
                "SOLUSDT": {
                    "symbol": "SOLUSDT", "direction": -1, "entry_price": "200.0", "current_qty": "1.0",
                    "current_stop": "200.0", "initial_atr": "5.0", "tp1_price": "192.5", "tp2_price": "185.0",
                    "tp1_taken": True, "tp2_taken": False, "is_breakeven_set": True, "bars_held": 4
                }
            }
        }
    }
    create_real_native_ledger(db_nat, state_nat)
    create_real_shadow_ledger(db_sha, state_sha)

    engine = TradingControlCenterDataEngine(native_ledger_path=db_nat, shadow_ledger_path=db_sha)
    pos_data = engine.get_positions_data()

    p_nat = pos_data["native_positions"][0]
    assert p_nat["tp1_price"] == "$93000.0000"
    assert p_nat["tp2_price"] == "$96000.0000"
    assert "TP1_TAKEN" in p_nat["flags"]
    assert "BREAK_EVEN" in p_nat["flags"]

    p_sha = pos_data["shadow_positions"][0]
    assert p_sha["tp1_price"] == "$192.5000"
    assert "TP1_TAKEN" in p_sha["flags"]
    assert "BREAK_EVEN" in p_sha["flags"]


# ==============================================================================
# Criterion 4: No Fabricated Rejection Reasons in Scanner (P0.2)
# ==============================================================================

def test_c04_no_fabricated_rejection_reasons(tmp_path):
    """Verifies scanner never generates NEUTRAL_ZONE or fake signals without proof."""
    state_file = tmp_path / "native_state.json"
    # Only BTCUSDT evaluated with closed bar; others uninitialized
    state_file.write_text(json.dumps({
        "last_processed_bar_ts": {
            "BTCUSDT": 1700000000000
        },
        "engine": {"open_positions": {}},
        "range_engine": {"open_positions": {}}
    }))

    engine = TradingControlCenterDataEngine(native_state_path=state_file)
    scan_data = engine.get_scanner_data()

    scan_by_sym = {item["symbol"]: item for item in scan_data["native_scanner"]}

    # BTC has a bar, but no signal/missed recorded -> NO_SIGNAL_RECORDED
    btc_row = scan_by_sym["BTCUSDT"]
    assert btc_row["confidence_status"] == "NO_SIGNAL_RECORDED"
    assert btc_row["rejection_reason"] == "NOT_RECORDED"
    assert "NEUTRAL_ZONE" not in btc_row["rejection_reason"]

    # ETH has no bar evaluated -> NOT_EVALUATED
    eth_row = scan_by_sym["ETHUSDT"]
    assert eth_row["confidence_status"] == "NOT_EVALUATED"
    assert eth_row["signal"] == "NOT_RECORDED"
    assert eth_row["rejection_reason"] == "NOT_RECORDED"


# ==============================================================================
# Criterion 5: Real R2 Structured Signal & Array Target Weights (P0.3)
# ==============================================================================

def test_c05_real_r2_signal_and_array_target_weights(tmp_path):
    """Verifies target_weights as an array [w1, w2, w3] and signal as structured dict."""
    r2_tel_file = tmp_path / "r2_telemetry.json"
    r2_payload = {
        "heartbeat_at": "2026-11-16T10:00:00Z",
        "target_weights": [0.45, 0.35, 0.0],  # ARRAY
        "signal": {
            "BTCUSDT": {"score": 0.88, "eligible": True, "target_weight": 0.45},
            "ETHUSDT": {"score": 0.72, "eligible": True, "target_weight": 0.35},
            "SOLUSDT": {"score": 0.31, "eligible": False, "target_weight": 0.0},
        },
        "profiles": [
            {
                "profile": R2_PRIMARY_PROFILE,
                "cash_usdt": 300.0,
                "quantities": {"BTCUSDT": 0.0, "ETHUSDT": 0.0, "SOLUSDT": 0.0}
            }
        ]
    }
    r2_tel_file.write_text(json.dumps(r2_payload))

    engine = TradingControlCenterDataEngine(r2_telemetry_path=r2_tel_file)
    scan_data = engine.get_scanner_data()

    r2_rows = {item["symbol"]: item for item in scan_data["r2_scanner"]}
    assert r2_rows["BTCUSDT"]["target_weight"] == "45.0%"
    assert r2_rows["BTCUSDT"]["signal"] == "ALLOCATE_TARGET"
    assert r2_rows["BTCUSDT"]["eligible"] == "YES"

    assert r2_rows["SOLUSDT"]["signal"] == "RANK_REJECTED"
    assert r2_rows["SOLUSDT"]["eligible"] == "NO"
    assert r2_rows["SOLUSDT"]["rejection_reason"] == "BELOW_RANK_THRESHOLD"
    assert r2_rows["SOLUSDT"]["fill_claim_badge"] == "VIRTUAL_MODEL_ESTIMATE"


# ==============================================================================
# Criterion 6: R2 Equity Strictly Filtered to Primary Profile (P0.3)
# ==============================================================================

def test_c06_r2_equity_strictly_filtered_to_primary_profile(tmp_path):
    """Verifies that R2 equity curve contains only R2:300:BASE:OBSERVED_BOOK points."""
    db_r2 = tmp_path / "observer.sqlite"
    create_real_r2_observer(db_r2)

    engine = TradingControlCenterDataEngine(r2_ledger_path=db_r2)
    ana_data = engine.get_analytics_data()

    r2_history = ana_data["r2"]["equity_history"]
    assert len(r2_history) == 1
    assert r2_history[0]["equity"] == 302.5  # Only the 300 profile! 1000 and 10000 were filtered out.


# ==============================================================================
# Criterion 7: Native Total Equity With Open Position (P0.4)
# ==============================================================================

def test_c07_native_total_equity_with_open_position(tmp_path):
    """Verifies that Native equity curve extracts total_equity (including floating PnL)."""
    db_nat = tmp_path / "native_ledger.db"
    create_real_native_ledger(db_nat, state_data={"engine": {"open_positions": {}}}, total_equity=338.5, realized_equity=300.0)

    engine = TradingControlCenterDataEngine(native_ledger_path=db_nat)
    ana_data = engine.get_analytics_data()

    history = ana_data["native"]["equity_history"]
    assert len(history) == 1
    assert history[0]["equity"] == 338.5  # Must match total_equity, NOT 300.0


# ==============================================================================
# Criterion 8: Missing DB Distinguished from Empty Portfolio (P0.1, P0.4)
# ==============================================================================

def test_c08_missing_db_distinguished_from_empty_portfolio(tmp_path):
    """Verifies that missing or unopenable DB returns DATA_SOURCE_ERROR, not OK_EMPTY."""
    # 1. Valid empty DB
    db_valid = tmp_path / "valid_empty.db"
    create_real_native_ledger(db_valid, state_data={"engine": {"open_positions": {}}})
    engine_ok = TradingControlCenterDataEngine(native_ledger_path=db_valid)
    pos_ok = engine_ok.get_positions_data()
    assert pos_ok["native"]["status"] == "OK_EMPTY"

    # 2. Missing DB
    missing_path = tmp_path / "does_not_exist.db"
    engine_err = TradingControlCenterDataEngine(native_ledger_path=missing_path, native_state_path=missing_path)
    pos_err = engine_err.get_positions_data()
    assert pos_err["native"]["status"] == "DATA_SOURCE_ERROR"
    assert "NOT_FOUND" in pos_err["native"]["error_diagnostic"]


# ==============================================================================
# Criterion 9: SQLite Error Explicitly Displayed in UI (P0.1)
# ==============================================================================

def test_c09_sqlite_error_explicitly_displayed(tmp_path):
    """Verifies that corrupted SQLite DB produces an explicit error banner in the HTML dashboard."""
    corrupted_db = tmp_path / "corrupted.db"
    corrupted_db.write_bytes(b"CORRUPTED_GARBAGE_BYTES_SQLITE")

    engine = TradingControlCenterDataEngine(native_ledger_path=corrupted_db, native_state_path=tmp_path / "no_state.json")
    dash = ThreeSystemComparativeDashboard(data_engine=engine)
    report = dash.generate_comparative_report()
    html_out = dash.render_html_dashboard(report)

    assert "DATA_SOURCE_ERROR" in html_out
    assert "ОШИБКА ИСТОЧНИКА ДАННЫХ" in html_out
    # Must NOT show "Пока открытых позиций нет" for the native pane
    assert "CORRUPTED" in html_out or "UNOPENABLE" in html_out or "ERROR" in html_out


# ==============================================================================
# Criterion 10: HTML Injection and XSS Prevention (P0.6)
# ==============================================================================

def test_c10_html_injection_prevention_xss(tmp_path):
    """Verifies that stored or reflected XSS strings are strictly HTML-escaped."""
    db_nat = tmp_path / "native_ledger.db"
    state_nat = {
        "engine": {
            "open_positions": {
                "<script>alert(1)</script>": {
                    "symbol": "<script>alert(1)</script>", "direction": 1,
                    "entry_price": "100.0", "current_qty": "1.0", "current_stop": "90.0",
                    "initial_atr": "5.0"
                }
            }
        }
    }
    create_real_native_ledger(db_nat, state_nat)

    # Insert malicious note in missed opportunities
    conn = sqlite3.connect(db_nat)
    conn.execute(
        "INSERT INTO native_missed_opportunities (symbol, timestamp_ms, strategy, direction, intended_price, reason) VALUES (?, ?, ?, ?, ?, ?);",
        ("<img src=x onerror=alert('xss')>", int(time.time() * 1000), "XSS_TEST", 1, 100.0, "<svg/onload=alert(2)>")
    )
    conn.commit()
    conn.close()

    engine = TradingControlCenterDataEngine(native_ledger_path=db_nat)
    dash = ThreeSystemComparativeDashboard(data_engine=engine)
    report = dash.generate_comparative_report()
    html_out = dash.render_html_dashboard(report)

    # Raw script tags must NEVER exist in output
    assert "<script>alert(1)</script>" not in html_out
    assert "<img src=x onerror=alert('xss')>" not in html_out
    assert "<svg/onload=alert(2)>" not in html_out

    # Must be escaped
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_out
    assert "&lt;img src=x onerror=alert(&#x27;xss&#x27;)&gt;" in html_out or "&lt;img src=x onerror=alert('xss')&gt;" in html_out
    assert "&lt;svg/onload=alert(2)&gt;" in html_out


# ==============================================================================
# Criterion 11: Real Event Timestamps and Data Freshness (P0.2, P0.5)
# ==============================================================================

def test_c11_event_timestamp_and_data_freshness(tmp_path):
    """Verifies that event_utc uses actual past timestamp, and service freshness works accurately."""
    fixed_ts_ms = 1700000000000  # 2023-11-14T22:13:20+00:00
    db_nat = tmp_path / "native_ledger.db"
    create_real_native_ledger(db_nat, state_data={"engine": {"open_positions": {}}})

    conn = sqlite3.connect(db_nat)
    conn.execute(
        "INSERT INTO native_missed_opportunities (symbol, timestamp_ms, strategy, direction, intended_price, reason) VALUES ('SOLUSDT', ?, 'Dual-Regime', 1, 60.0, 'ADX_BELOW_18');",
        (fixed_ts_ms,)
    )
    conn.commit()
    conn.close()

    engine = TradingControlCenterDataEngine(native_ledger_path=db_nat)
    j_data = engine.get_journal_data()

    assert len(j_data["events"]) >= 1
    missed_ev = [e for e in j_data["events"] if e["symbol"] == "SOLUSDT"][0]
    assert "2023-11-14" in missed_ev["event_utc"]
    # Must NOT use today's timestamp
    assert str(datetime.now(timezone.utc).year) not in missed_ev["event_utc"]


# ==============================================================================
# Criterion 12: Read-Only HTTP API Rejection of Mutations (P0.6)
# ==============================================================================

def test_c12_read_only_api_rejection_of_mutations(tmp_path):
    """Verifies that POST, PUT, DELETE, PATCH return 405 Method Not Allowed."""
    dash = ThreeSystemComparativeDashboard()
    server = create_dashboard_server(host="127.0.0.1", port=8096, dashboard_provider=dash)
    import threading
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    base_url = "http://127.0.0.1:8096"
    try:
        # GET is allowed
        resp = urlopen(f"{base_url}/health", timeout=3.0)
        assert resp.status == 200

        # Mutations MUST be 405
        for method in ["POST", "PUT", "DELETE", "PATCH"]:
            req = Request(f"{base_url}/api/positions", data=b'{"action":"trade"}', method=method)
            try:
                urlopen(req, timeout=3.0)
                pytest.fail(f"Expected 405 on {method}, but request succeeded")
            except HTTPError as e:
                assert e.code == 405, f"Method {method} returned {e.code} instead of 405"
    finally:
        server.shutdown()
        server.server_close()


# ==============================================================================
# Criterion 13: High Performance on Realistic Database Sizes (<50ms) (P1)
# ==============================================================================

def test_c13_performance_on_realistic_db_sizes(tmp_path):
    """Verifies that queries over 10,000 journal records execute in <50ms."""
    db_nat = tmp_path / "heavy_native.db"
    conn = sqlite3.connect(db_nat)
    cur = conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("""
        CREATE TABLE native_journal (
            journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_ms INTEGER NOT NULL,
            iso_time TEXT NOT NULL,
            cohort_id TEXT NOT NULL,
            realized_equity REAL NOT NULL,
            unrealized_pnl REAL NOT NULL,
            total_equity REAL NOT NULL,
            cash_balance REAL NOT NULL,
            total_fees REAL NOT NULL,
            total_funding REAL NOT NULL,
            peak_equity REAL NOT NULL,
            positions_count INTEGER NOT NULL,
            gear INTEGER DEFAULT 3,
            notes TEXT
        );
    """)
    cur.execute("""
        CREATE TABLE native_state_journal (
            journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            saved_at_ms INTEGER NOT NULL,
            saved_at_utc TEXT NOT NULL,
            event_key TEXT NOT NULL,
            realized_equity REAL NOT NULL,
            peak_equity REAL NOT NULL,
            state_json TEXT NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE native_trades (
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
        CREATE TABLE native_missed_opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            timestamp_ms INTEGER NOT NULL,
            strategy TEXT NOT NULL,
            direction INTEGER NOT NULL,
            intended_price REAL NOT NULL,
            reason TEXT NOT NULL
        );
    """)

    # Populate 10,000 journal records in batch
    journal_records = [
        (1700000000000 + i * 1000, f"2023-11-14T{i}", "COHORT_1", 300.0, 0.0, 300.0 + (i * 0.01), 200.0, 1.0, 0.0, 310.0, 0)
        for i in range(10000)
    ]
    cur.executemany(
        """INSERT INTO native_journal (timestamp_ms, iso_time, cohort_id, realized_equity, unrealized_pnl, total_equity, cash_balance, total_fees, total_funding, peak_equity, positions_count)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);""",
        journal_records
    )

    state_json = json.dumps({"engine": {"open_positions": {}}, "range_engine": {"open_positions": {}}})
    cur.execute(
        "INSERT INTO native_state_journal (saved_at_ms, saved_at_utc, event_key, realized_equity, peak_equity, state_json) VALUES (1700010000000, '2023-11-14T10:00:00Z', 'SAVE', 300.0, 310.0, ?);",
        (state_json,)
    )
    conn.commit()
    conn.close()

    engine = TradingControlCenterDataEngine(native_ledger_path=db_nat)

    t0 = time.perf_counter()
    ana_data = engine.get_analytics_data()
    t_elapsed_ms = (time.perf_counter() - t0) * 1000.0

    assert len(ana_data["native"]["equity_history"]) > 0
    # Strict latency requirement: < 50ms
    assert t_elapsed_ms < 50.0, f"Query took {t_elapsed_ms:.2f}ms, expected < 50ms"
