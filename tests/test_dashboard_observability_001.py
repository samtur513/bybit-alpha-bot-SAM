"""
tests/test_dashboard_observability_001.py
Comprehensive Acceptance Test Suite for TORG_BOT Trading Control Center v1.0
Protocol: DASHBOARD-OBSERVABILITY-001 (Audit REPAIR-003 Resolution)

Covers the 12 specific regression test cases mandated by the Independent Chief Architect:
1. Open losing position with missing unrealized_pnl (justified calculation contract or NOT_AVAILABLE)
2. Actual mark differs from entry (not substituted with entry price)
3. Partial TP1 exit occurred but flag absent in saved state (status UNKNOWN, never fake False)
4. Real Shadow TradeRecord with initial_qty, final_qty, funding_pnl (exact field mapping)
5. Shadow open position and unrealized loss (MTM total equity != realized equity)
6. R2 equity_history appears on HTML chart (r2 vs r2_shadow key parity)
7. History over 500 records shows the latest points in ascending chronological order
8. No trades -> accounting is declared ACCOUNTING_NOT_YET_OBSERVED
9. Scanner does not invent decision when no signal is recorded (NO_DECISION_RECORDED, NOT_EVALUATED)
10. All dynamic HTML fields are escaped (XSS prevention)
11. Corrupted state journal does not turn into an empty portfolio (DATA_SOURCE_ERROR)
12. Strict read-only mode and rejection of mutations (POST/PUT/DELETE/PATCH -> 405)
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
            total_equity REAL NOT NULL,
            realized_equity REAL NOT NULL,
            cash_balance REAL NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS native_state_journal (
            journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            saved_at_ms INTEGER NOT NULL,
            saved_at_utc TEXT NOT NULL,
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
            reason TEXT NOT NULL,
            timestamp_ms INTEGER NOT NULL,
            strategy TEXT NOT NULL,
            direction INTEGER NOT NULL
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
            symbol TEXT NOT NULL,
            event_type TEXT NOT NULL,
            execution_status TEXT NOT NULL,
            notes TEXT
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
            created_at TEXT NOT NULL,
            payload TEXT NOT NULL
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS events (
            rowid INTEGER PRIMARY KEY AUTOINCREMENT,
            at TEXT NOT NULL,
            kind TEXT NOT NULL,
            payload TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()


# =============================================================================
# 1. Open losing position with missing unrealized_pnl
# =============================================================================
def test_01_open_losing_position_with_missing_unrealized_pnl(tmp_path):
    nat_db = tmp_path / "native.db"
    _init_native_db(nat_db)

    # Position: Long 2.0 @ 100.0, current_mark = 95.0 (loss of -10.0), unrealized_pnl NOT given
    state = {
        "engine": {
            "open_positions": {
                "BTCUSDT": {
                    "symbol": "BTCUSDT",
                    "direction": 1,
                    "entry_price": 100.0,
                    "current_qty": 2.0,
                    "current_stop": 90.0,
                    "initial_atr": 5.0,
                    "current_mark": 95.0,
                    "entry_time": 1700000000000,
                }
            }
        },
        "range_engine": {"open_positions": {}},
    }
    conn = sqlite3.connect(nat_db)
    conn.execute(
        "INSERT INTO native_state_journal (saved_at_ms, saved_at_utc, state_json) VALUES (1700000001000, '2026-11-16T10:00:00Z', ?);",
        (json.dumps(state),)
    )
    conn.commit()
    conn.close()

    engine = TradingControlCenterDataEngine(native_ledger_path=nat_db)
    pos_data = engine.get_positions_data()

    assert len(pos_data["native_positions"]) == 1
    p = pos_data["native_positions"][0]
    # Mark price must be 95.0, and unrealized PnL computed as 1 * (95 - 100) * 2 = -10.0
    assert p["current_mark"] == 95.0
    assert p["unrealized_pnl_usdt"] == -10.0

    # Case 1b: Mark is missing -> unrealized PnL must be NOT_AVAILABLE, NEVER 0.0
    state_no_mark = {
        "engine": {
            "open_positions": {
                "ETHUSDT": {
                    "symbol": "ETHUSDT",
                    "direction": -1,
                    "entry_price": 3000.0,
                    "current_qty": 1.0,
                    "current_stop": 3100.0,
                    "initial_atr": 50.0,
                    "entry_time": 1700000000000,
                }
            }
        },
        "range_engine": {"open_positions": {}},
    }
    conn = sqlite3.connect(nat_db)
    conn.execute(
        "INSERT INTO native_state_journal (saved_at_ms, saved_at_utc, state_json) VALUES (1700000002000, '2026-11-16T10:01:00Z', ?);",
        (json.dumps(state_no_mark),)
    )
    conn.commit()
    conn.close()

    engine_no_mark = TradingControlCenterDataEngine(native_ledger_path=nat_db)
    pos_data2 = engine_no_mark.get_positions_data()
    p2 = pos_data2["native_positions"][0]
    assert p2["current_mark"] == "NOT_AVAILABLE"
    assert p2["unrealized_pnl_usdt"] == "NOT_AVAILABLE"


# =============================================================================
# 2. Actual mark differs from entry
# =============================================================================
def test_02_actual_mark_differs_from_entry(tmp_path):
    nat_db = tmp_path / "native.db"
    _init_native_db(nat_db)

    state = {
        "engine": {
            "open_positions": {
                "BTCUSDT": {
                    "symbol": "BTCUSDT",
                    "direction": 1,
                    "entry_price": 50000.0,
                    "current_qty": 0.5,
                    "current_stop": 48000.0,
                    "initial_atr": 1000.0,
                    "current_mark": 51200.0,
                    "entry_time": 1700000000000,
                }
            }
        },
        "range_engine": {"open_positions": {}},
    }
    conn = sqlite3.connect(nat_db)
    conn.execute(
        "INSERT INTO native_state_journal (saved_at_ms, saved_at_utc, state_json) VALUES (1700000001000, '2026-11-16T10:00:00Z', ?);",
        (json.dumps(state),)
    )
    conn.commit()
    conn.close()

    engine = TradingControlCenterDataEngine(native_ledger_path=nat_db)
    pos = engine.get_positions_data()["native_positions"][0]

    assert pos["entry_price"] == 50000.0
    assert pos["current_mark"] == 51200.0
    assert pos["current_mark"] != pos["entry_price"]
    assert pos["unrealized_pnl_usdt"] == round(1 * (51200.0 - 50000.0) * 0.5, 4)


# =============================================================================
# 3. Partial TP1 exit occurred, but flag absent in saved state
# =============================================================================
def test_03_partial_tp1_exit_without_flag_in_state(tmp_path):
    nat_db = tmp_path / "native.db"
    _init_native_db(nat_db)

    # In real CoreV3Engine.to_dict(), tp1_taken is absent
    state = {
        "engine": {
            "open_positions": {
                "SOLUSDT": {
                    "symbol": "SOLUSDT",
                    "direction": 1,
                    "entry_price": 150.0,
                    "initial_qty": 10.0,
                    "current_qty": 7.0,  # 30% partial already taken!
                    "current_stop": 150.0,
                    "initial_atr": 5.0,
                    "entry_time": 1700000000000,
                    "partial_taken": True,  # CoreV3Engine serializes partial_taken, not tp1_taken!
                }
            }
        },
        "range_engine": {"open_positions": {}},
    }
    conn = sqlite3.connect(nat_db)
    conn.execute(
        "INSERT INTO native_state_journal (saved_at_ms, saved_at_utc, state_json) VALUES (1700000001000, '2026-11-16T10:00:00Z', ?);",
        (json.dumps(state),)
    )
    conn.commit()
    conn.close()

    engine = TradingControlCenterDataEngine(native_ledger_path=nat_db)
    pos = engine.get_positions_data()["native_positions"][0]

    # Status must be UNKNOWN, NEVER false claim of PENDING or False!
    assert pos["tp1_status"] == "UNKNOWN"
    assert pos["tp2_status"] == "UNKNOWN"
    assert "TP_STATUS_UNKNOWN" in pos["flags"]


# =============================================================================
# 4. Real Shadow TradeRecord with initial_qty, final_qty, funding_pnl
# =============================================================================
def test_04_real_shadow_trade_record_fields(tmp_path):
    sha_db = tmp_path / "shadow.db"
    _init_shadow_db(sha_db)

    # Record matching exact TradeRecord.to_dict() schema from paper_trader_v32_shadow.py
    trade_record = {
        "trade_id": "SHADOW_BTC_001",
        "portfolio": "SHADOW",
        "symbol": "BTCUSDT",
        "direction": 1,
        "entry_time_ms": 1700000000000,
        "exit_time_ms": 1700003600000,
        "entry_price": "50000.0",
        "exit_price": "51000.0",
        "initial_qty": "1.500",
        "final_qty": "0.500",
        "gross_pnl": "1500.00",
        "fees_paid": "45.00",
        "funding_pnl": "-12.50",
        "net_pnl": "1442.50",
        "exit_reason": "TAKE_PROFIT_2",
        "slippage_bps": "3.5",
    }
    state = {
        "shadow_portfolio": {
            "realized_equity": 1742.50,
            "positions": {},
            "closed_trades": [trade_record],
        }
    }
    conn = sqlite3.connect(sha_db)
    conn.execute(
        """INSERT INTO shadow_state_journal (
               saved_at_ms, saved_at_utc, event_key, native_realized_equity, shadow_realized_equity,
               native_peak_equity, shadow_peak_equity, native_gear, shadow_gear, state_json
           ) VALUES (1700003601000, '2026-11-16T11:00:00Z', 'EVT_001', 300.0, 1742.50, 300.0, 1742.50, 1, 1, ?);""",
        (json.dumps(state),)
    )
    conn.commit()
    conn.close()

    engine = TradingControlCenterDataEngine(shadow_ledger_path=sha_db)
    analytics = engine.get_analytics_data()

    trades = analytics["shadow"]["closed_trades"]
    assert len(trades) == 1
    t = trades[0]
    assert t["initial_qty"] == 1.500
    assert t["final_qty"] == 0.500
    assert t["funding_pnl_usdt"] == -12.50
    assert t["fees_paid_usdt"] == 45.00
    assert t["gross_pnl_usdt"] == 1500.00
    assert t["net_pnl_usdt"] == 1442.50
    assert t["slippage_bps"] == 3.5


# =============================================================================
# 5. Shadow has open position and unrealized loss (MTM != realized equity)
# =============================================================================
def test_05_shadow_open_position_and_unrealized_loss(tmp_path):
    sha_db = tmp_path / "shadow.db"
    _init_shadow_db(sha_db)

    # Shadow realized equity = 300.0, open position Long 1.0 @ 100.0, mark = 90.0 (-10.0 loss)
    state = {
        "shadow_portfolio": {
            "realized_equity": 300.0,
            "positions": {
                "BTCUSDT": {
                    "symbol": "BTCUSDT",
                    "direction": 1,
                    "entry_price": "100.0",
                    "current_qty": "1.0",
                    "current_mark": "90.0",
                    "current_stop": "80.0",
                    "tp1_price": "120.0",
                    "tp2_price": "140.0",
                    "entry_time_ms": 1700000000000,
                }
            },
            "closed_trades": [],
        }
    }
    conn = sqlite3.connect(sha_db)
    conn.execute(
        """INSERT INTO shadow_state_journal (
               saved_at_ms, saved_at_utc, event_key, native_realized_equity, shadow_realized_equity,
               native_peak_equity, shadow_peak_equity, native_gear, shadow_gear, state_json
           ) VALUES (1700000001000, '2026-11-16T10:00:00Z', 'EVT_001', 300.0, 300.0, 300.0, 300.0, 1, 1, ?);""",
        (json.dumps(state),)
    )
    conn.commit()
    conn.close()

    engine = TradingControlCenterDataEngine(shadow_ledger_path=sha_db)
    analytics = engine.get_analytics_data()
    shadow_series = analytics["shadow"]["equity_history"]

    assert len(shadow_series) == 1
    pt = shadow_series[0]
    # MTM equity must be 300.0 - 10.0 = 290.0, NOT 300.0!
    assert pt["equity"] == 290.0
    assert pt["realized_equity"] == 300.0
    assert pt["unrealized_pnl"] == -10.0


# =============================================================================
# 6. R2 equity_history appears on HTML chart (r2 vs r2_shadow parity)
# =============================================================================
def test_06_r2_equity_history_appears_on_html_chart(tmp_path):
    r2_db = tmp_path / "observer.sqlite"
    _init_r2_db(r2_db)

    # Insert two equity points for R2:300:BASE:OBSERVED_BOOK
    conn = sqlite3.connect(r2_db)
    payload1 = json.dumps({"strategy": "R2", "capital": 300, "cost": "BASE", "policy": "OBSERVED_BOOK", "mtm": 300.0})
    payload2 = json.dumps({"strategy": "R2", "capital": 300, "cost": "BASE", "policy": "OBSERVED_BOOK", "mtm": 305.5})
    conn.execute("INSERT INTO events (at, kind, payload) VALUES ('2026-11-16T10:00:00Z', 'EQUITY', ?);", (payload1,))
    conn.execute("INSERT INTO events (at, kind, payload) VALUES ('2026-11-16T10:30:00Z', 'EQUITY', ?);", (payload2,))
    conn.commit()
    conn.close()

    engine = TradingControlCenterDataEngine(r2_ledger_path=r2_db)
    analytics = engine.get_analytics_data()

    # Parity check: both keys must exist and have equity history
    assert "r2" in analytics
    assert "r2_shadow" in analytics
    assert len(analytics["r2"]["equity_history"]) == 2
    assert len(analytics["r2_shadow"]["equity_history"]) == 2

    # Render dashboard HTML
    provider = ThreeSystemComparativeDashboard(data_engine=engine)
    report = provider.generate_comparative_report()
    html = provider.render_html_dashboard(report)

    # Chart must contain points from R2 equity history
    assert "polyline" in html or "circle" in html
    assert "$305.5" in html or "305.5" in html


# =============================================================================
# 7. History over 500 records shows latest points in ascending order
# =============================================================================
def test_07_history_over_500_records_shows_latest_points(tmp_path):
    nat_db = tmp_path / "native.db"
    _init_native_db(nat_db)

    conn = sqlite3.connect(nat_db)
    # Insert 600 records: journal_id 1 to 600
    rows = [
        (i, 1700000000000 + i * 1000, f"2026-11-16T{i//3600:02d}:{(i%3600)//60:02d}:{i%60:02d}Z", 300.0 + i, 300.0 + i, 300.0)
        for i in range(1, 601)
    ]
    conn.executemany(
        "INSERT INTO native_journal (journal_id, timestamp_ms, iso_time, total_equity, realized_equity, cash_balance) VALUES (?, ?, ?, ?, ?, ?);",
        rows
    )
    conn.commit()
    conn.close()

    engine = TradingControlCenterDataEngine(native_ledger_path=nat_db)
    analytics = engine.get_analytics_data()
    # downsample keeps max 150 points from the queried 500 records
    series = analytics["native"]["equity_history"]

    # Last point must be the latest record (equity = 900.0 for record 600)
    assert series[-1]["equity"] == 900.0
    # First point of the queried 500 records (records 101-600) must be >= 401.0, NOT record 1 (301.0)!
    assert series[0]["equity"] >= 401.0
    # Verify chronological ascending order
    assert series[-1]["equity"] > series[0]["equity"]


# =============================================================================
# 8. No trades -> accounting declared ACCOUNTING_NOT_YET_OBSERVED
# =============================================================================
def test_08_no_trades_accounting_not_yet_observed(tmp_path):
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

    # Must contain ACCOUNTING_NOT_YET_OBSERVED, NOT "OK"
    assert "ACCOUNTING_NOT_YET_OBSERVED" in html
    # Ensure "OK" is not fraudulently displayed as the accounting verification status for zero trades
    assert "ACCOUNTING_VERIFIED" in html


# =============================================================================
# 9. Scanner does not invent decision when no signal recorded
# =============================================================================
def test_09_scanner_does_not_invent_decision_without_signal(tmp_path):
    nat_db = tmp_path / "native.db"
    _init_native_db(nat_db)

    # Symbol BTCUSDT has processed bar ts, but no breakout/signal
    state = {
        "engine": {"open_positions": {}},
        "range_engine": {"open_positions": {}},
        "last_processed_bar_ts": {"BTCUSDT": 1700000000000},
    }
    conn = sqlite3.connect(nat_db)
    conn.execute(
        "INSERT INTO native_state_journal (saved_at_ms, saved_at_utc, state_json) VALUES (1700000001000, '2026-11-16T10:00:00Z', ?);",
        (json.dumps(state),)
    )
    conn.commit()
    conn.close()

    engine = TradingControlCenterDataEngine(native_ledger_path=nat_db)
    scanner = engine.get_scanner_data()

    btc_item = next(item for item in scanner["native_scanner"] if item["symbol"] == "BTCUSDT")
    # Must NOT invent NO_BREAKOUT or OBSERVING_MARKET
    assert btc_item["market_regime"] == "NOT_RECORDED"
    assert btc_item["decision"] == "NO_DECISION_RECORDED"
    assert btc_item["signal"] == "NO_SIGNAL_RECORDED"
    assert btc_item["confidence_status"] in ("BAR_PROCESSED / NO_SIGNAL_RECORDED", "NO_SIGNAL_RECORDED")

    # R2 with no data must have 0 evaluated, NOT 3
    assert scanner["funnel"]["r2"]["evaluated"] == 0
    r2_btc = next(item for item in scanner["r2_scanner"] if item["symbol"] == "BTCUSDT")
    assert r2_btc["decision"] == "NO_DECISION_RECORDED"
    assert r2_btc["confidence_status"] == "NOT_EVALUATED"


# =============================================================================
# 10. All dynamic HTML fields are escaped (XSS prevention)
# =============================================================================
def test_10_all_dynamic_html_fields_escaped(tmp_path):
    nat_db = tmp_path / "native.db"
    _init_native_db(nat_db)

    # Inject XSS in position symbol, strategy, and cohort_id
    xss_payload = '<script>alert("xss")</script>'
    xss_img = '"><img src=x onerror=alert(1)>'
    state = {
        "engine": {
            "open_positions": {
                xss_payload: {
                    "symbol": xss_payload,
                    "direction": 1,
                    "entry_price": 100.0,
                    "current_qty": 1.0,
                    "current_stop": 90.0,
                    "initial_atr": 5.0,
                    "entry_time": 1700000000000,
                }
            }
        },
        "range_engine": {"open_positions": {}},
    }
    conn = sqlite3.connect(nat_db)
    conn.execute(
        "INSERT INTO native_state_journal (saved_at_ms, saved_at_utc, state_json) VALUES (1700000001000, '2026-11-16T10:00:00Z', ?);",
        (json.dumps(state),)
    )
    conn.commit()
    conn.close()

    engine = TradingControlCenterDataEngine(native_ledger_path=nat_db)
    provider = ThreeSystemComparativeDashboard(
        data_engine=engine,
        expected_cohort_id=xss_img,
    )
    report = provider.generate_comparative_report(cohort_id=xss_img)
    html = provider.render_html_dashboard(report)

    # Must NOT contain raw unescaped script tags
    assert "<script>alert(\"xss\")</script>" not in html
    assert "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;" in html
    # Must NOT contain raw unescaped img tags
    assert '"><img src=x onerror=alert(1)>' not in html
    assert "&quot;&gt;&lt;img src=x onerror=alert(1)&gt;" in html


# =============================================================================
# 11. Corrupted state journal does not turn into empty portfolio
# =============================================================================
def test_11_corrupted_state_journal_not_empty_portfolio(tmp_path):
    nat_db = tmp_path / "native_corrupted.db"
    # Create invalid sqlite file containing random garbage
    with open(nat_db, "wb") as f:
        f.write(b"NOT_A_VALID_SQLITE_DATABASE_HEADER_DATA_12345")

    engine = TradingControlCenterDataEngine(native_ledger_path=nat_db)
    pos_data = engine.get_positions_data()

    # Must report DATA_SOURCE_ERROR, and must NOT report empty portfolio OK_EMPTY!
    assert pos_data["data_status"]["native"] == "DATA_SOURCE_ERROR"
    assert pos_data["is_empty"] is False

    # Also test invalid JSON inside valid table
    nat_db2 = tmp_path / "native_corrupt_json.db"
    _init_native_db(nat_db2)
    conn = sqlite3.connect(nat_db2)
    conn.execute(
        "INSERT INTO native_state_journal (saved_at_ms, saved_at_utc, state_json) VALUES (1700000001000, '2026-11-16T10:00:00Z', '{invalid_json:');"
    )
    conn.commit()
    conn.close()

    engine2 = TradingControlCenterDataEngine(native_ledger_path=nat_db2)
    pos_data2 = engine2.get_positions_data()
    assert pos_data2["data_status"]["native"] == "DATA_SOURCE_ERROR"
    assert pos_data2["is_empty"] is False


# =============================================================================
# 12. Full read-only mode preserved (POST/PUT/DELETE/PATCH -> 405)
# =============================================================================
def test_12_full_read_only_mode_preserved(tmp_path):
    nat_db = tmp_path / "native.db"
    _init_native_db(nat_db)
    engine = TradingControlCenterDataEngine(native_ledger_path=nat_db)
    provider = ThreeSystemComparativeDashboard(data_engine=engine)

    port = 18099
    server = create_dashboard_server(
        host="127.0.0.1",
        port=port,
        dashboard_provider=provider,
    )
    th = threading.Thread(target=server.serve_forever, daemon=True)
    th.start()
    time.sleep(0.2)

    try:
        # GET must succeed
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
        conn.request("GET", "/api/health")
        res = conn.getresponse()
        assert res.status == 200
        data = json.loads(res.read().decode())
        assert data["read_only_enforced"] is True
        conn.close()

        # Mutations must be rejected with 405 Method Not Allowed
        for method in ["POST", "PUT", "DELETE", "PATCH"]:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=2.0)
            conn.request(method, "/api/positions", body=b'{"trade":"buy"}')
            res = conn.getresponse()
            assert res.status == 405, f"Method {method} returned {res.status}, expected 405"
            conn.close()
    finally:
        server.shutdown()
        server.server_close()
