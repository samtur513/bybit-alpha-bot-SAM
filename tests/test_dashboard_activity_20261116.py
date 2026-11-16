"""Independent evidence-only regression tests for the Dashboard activity feed."""
import json
import sqlite3
from datetime import datetime, timezone

from v3_2.src.integrations.trading_control_center_data import TradingControlCenterDataEngine


def make_dbs(tmp_path):
    nat = tmp_path / "native.db"
    sha = tmp_path / "shadow.db"
    r2 = tmp_path / "observer.sqlite"
    with sqlite3.connect(nat) as db:
        db.executescript("""
        CREATE TABLE native_journal(
            journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp_ms INTEGER, notes TEXT);
        CREATE TABLE native_missed_opportunities(
            id INTEGER PRIMARY KEY AUTOINCREMENT, symbol TEXT,
            timestamp_ms INTEGER, reason TEXT);
        CREATE TABLE native_trades(
            trade_id TEXT PRIMARY KEY, symbol TEXT, exit_time_ms INTEGER,
            exit_price REAL, qty REAL, net_pnl REAL, exit_reason TEXT);
        CREATE TABLE native_funding_settlements(
            symbol TEXT, settlement_time_ms INTEGER, payment REAL);
        CREATE TABLE native_state_journal(
            journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
            state_json TEXT, saved_at_utc TEXT);
        """)
    with sqlite3.connect(sha) as db:
        db.executescript("""
        CREATE TABLE shadow_dedup_events(
            event_key TEXT PRIMARY KEY, exchange_ts INTEGER,
            symbol TEXT, processed_at_ms INTEGER);
        CREATE TABLE shadow_execution_events(
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            exchange_ts INTEGER, receive_ts INTEGER,
            symbol TEXT, event_type TEXT, fill_price REAL, qty REAL,
            realized_pnl REAL, execution_status TEXT);
        CREATE TABLE shadow_funding_settlements(
            symbol TEXT, settlement_time_ms INTEGER, payment REAL);
        """)
    with sqlite3.connect(r2) as db:
        db.executescript("""
        CREATE TABLE events(
            id TEXT PRIMARY KEY, kind TEXT, at TEXT, payload TEXT);
        CREATE TABLE accounts(id TEXT PRIMARY KEY,payload TEXT);
        """)
    return nat, sha, r2


def millis(iso):
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def test_journal_includes_proven_processed_bars_and_r2_daily_decision(tmp_path):
    nat, sha, r2 = make_dbs(tmp_path)
    bar_open = millis("2026-11-16T12:00:00Z")
    bar_close = bar_open + 3600000
    with sqlite3.connect(nat) as db:
        db.execute("INSERT INTO native_journal(timestamp_ms,notes) VALUES (?,?)",
                   (bar_close + 3000, "closed_bar_BTCUSDT"))
    with sqlite3.connect(sha) as db:
        db.execute("INSERT INTO shadow_dedup_events VALUES (?,?,?,?)",
                   ("BTCUSDT_1794830400000", bar_open, "BTCUSDT", bar_close + 5000))
    with sqlite3.connect(r2) as db:
        db.execute("INSERT INTO events VALUES (?,?,?,?)",
                   ("DECISION:2026-11-16", "DECISION",
                    "2026-11-16T00:00:11Z",
                    json.dumps({"eligible": [False, False, False], "R2": [0, 0, 0]})))
        db.execute("INSERT INTO events VALUES (?,?,?,?)",
                   ("EXECUTION:2026-11-16", "EXECUTION",
                    "2026-11-16T01:00:15Z",
                    json.dumps({"status": "ESTIMATES_RECORDED_NOT_ACTUAL_FILLS"})))
        # Lots of more recent Equity heartbeats must not hide earlier decisions.
        for i in range(200):
            db.execute("INSERT INTO events VALUES (?,?,?,?)",
                       (f"EQUITY:{i}", "EQUITY", "2026-11-16T12:00:00Z",
                        json.dumps({"strategy": "R2", "mtm": 300.0})))

    engine = TradingControlCenterDataEngine(
        native_ledger_path=nat, shadow_ledger_path=sha, r2_ledger_path=r2)
    journal = engine.get_journal_data(limit=120)
    events = journal["events"]
    kinds = {(e["system"], e["event_type"]) for e in events}
    assert ("NATIVE", "BAR_PROCESSED") in kinds
    assert ("SHADOW", "BAR_PROCESSED") in kinds
    assert ("CRYPTOLAB_R2", "DAILY_DECISION_RECORDED") in kinds
    assert ("CRYPTOLAB_R2", "VIRTUAL_EXECUTION_CYCLE") in kinds
    assert journal["total_events"] >= 4
    assert journal["r2_last_daily_decision_utc"] == "2026-11-16T00:00:11Z"
    shadow_bar = next(e for e in events if e["system"] == "SHADOW")
    assert shadow_bar["event_utc"].startswith("2026-11-16T13:00:00")
    assert shadow_bar["pnl_impact_usdt"] is None
    assert journal["source_status"] == {"native": "OK", "shadow": "OK", "r2": "OK"}


def test_journal_never_makes_trade_signal_from_bar(tmp_path):
    nat, sha, r2 = make_dbs(tmp_path)
    with sqlite3.connect(nat) as db:
        db.execute("INSERT INTO native_journal(timestamp_ms,notes) VALUES (?,?)",
                   (millis("2026-11-16T12:00:00Z"), "closed_bar_ETHUSDT"))
    journal = TradingControlCenterDataEngine(
        native_ledger_path=nat, shadow_ledger_path=sha,
        r2_ledger_path=r2).get_journal_data()
    native = [e for e in journal["events"] if e["system"] == "NATIVE"]
    assert len(native) == 1
    assert native[0]["event_type"] == "BAR_PROCESSED"
    assert not any(e["event_type"] == "SIGNAL_DETECTED" for e in native)


def test_scanner_uses_decision_timestamp_not_heartbeat(tmp_path):
    nat, sha, r2 = make_dbs(tmp_path)
    with sqlite3.connect(r2) as db:
        db.execute("INSERT INTO accounts VALUES (?,?)",
                   ("R2:300:BASE:OBSERVED_BOOK",
                    json.dumps({"cash":300.0,"qty":[0,0,0]})))
        db.execute("INSERT INTO events VALUES (?,?,?,?)",
                   ("DECISION:2026-11-16", "DECISION",
                    "2026-11-16T00:00:11Z", json.dumps({"eligible":[False]*3})))
    tel = tmp_path / "r2-telemetry.json"
    tel.write_text(json.dumps({
        "heartbeat_at": "2026-11-16T13:00:00Z",
        "target_weights": [0,0,0],
        "signal": {"BTCUSDT":{"score":-1.2,"eligible":False}},
    }))
    engine = TradingControlCenterDataEngine(
        native_ledger_path=nat, shadow_ledger_path=sha,
        r2_ledger_path=r2, r2_telemetry_path=tel)
    row = engine.get_scanner_data()["r2_scanner"][0]
    assert row["event_at"].startswith("2026-11-16T00:00:11")
    assert "13:00:00" not in row["event_at"]


def test_health_shows_proven_shadow_bar_age_separate_from_telemetry(tmp_path):
    nat, sha, r2 = make_dbs(tmp_path)
    with sqlite3.connect(sha) as db:
        db.execute("INSERT INTO shadow_dedup_events VALUES (?,?,?,?)",
                   ("x", millis("2026-11-16T11:00:00Z"),
                    "BTCUSDT", millis("2026-11-16T12:00:08Z")))
    health = TradingControlCenterDataEngine(
        native_ledger_path=nat, shadow_ledger_path=sha,
        r2_ledger_path=r2).get_health_data()
    assert health["market_activity"]["shadow"]["last_observation_utc"].startswith(
        "2026-11-16T12:00:00")
    assert health["services"][1]["status"] == "SERVICE_STATE_UNKNOWN"
    assert health["services"][1]["status"] != "TELEMETRY_FRESH"


def test_r2_equity_real_live_timestamps_only_one_actual_gap():
    """Captured R2 UTC sample cadence: 3603s/3656s are normal jitter, 8h is a gap."""
    from v3_2.src.integrations.three_system_dashboard import ThreeSystemComparativeDashboard
    timestamps = [
        "2026-11-16T00:00:11.795857+00:00",
        "2026-11-16T00:00:12.263874+00:00",
        "2026-11-16T01:00:15.636765+00:00",
        "2026-11-16T01:00:46.085704+00:00",
        "2026-11-16T09:20:21.740820+00:00",
        "2026-11-16T10:00:48.042497+00:00",
        "2026-11-16T11:00:03.826232+00:00",
        "2026-11-16T12:01:00.111746+00:00",
        "2026-11-16T13:00:38.123376+00:00",
    ]
    series = [{"timestamp_utc": t, "equity": 300.0} for t in timestamps]
    svg = ThreeSystemComparativeDashboard()._render_svg_equity_chart(series)
    assert svg.count('data-gap=') == 1, "Minute-scale scheduler jitter is not a data outage"
    assert "GAP 8h 19m" in svg
    assert "UTC / USD" in svg
    assert ">00:00<" in svg
    assert ">13:00<" in svg
    # Time-proportional gap should occupy >half the plotting width (~8h/13h).
    import re
    match = re.search(r'data-gap="1"[^>]+width="([0-9.]+)"', svg)
    assert match and float(match.group(1)) > 350.0


def test_hourly_r2_small_timestamp_jitter_has_zero_gaps():
    from v3_2.src.integrations.three_system_dashboard import ThreeSystemComparativeDashboard
    series = [
        {"timestamp_utc": "2026-11-16T11:00:03Z", "equity": 300.0},
        {"timestamp_utc": "2026-11-16T12:01:00Z", "equity": 300.2},
        {"timestamp_utc": "2026-11-16T13:00:38Z", "equity": 300.4},
    ]
    svg = ThreeSystemComparativeDashboard()._render_svg_equity_chart(series)
    assert 'data-gap=' not in svg
    assert "<polyline" in svg
    assert ">13:00<" in svg


def test_unpriced_mtm_is_never_connected_through_unknown_value():
    from v3_2.src.integrations.three_system_dashboard import ThreeSystemComparativeDashboard
    series = [
        {"timestamp_utc": "2026-11-16T10:00:00Z", "equity": 300},
        {"timestamp_utc": "2026-11-16T11:00:00Z", "equity": None},
        {"timestamp_utc": "2026-11-16T12:00:00Z", "equity": 290},
    ]
    svg = ThreeSystemComparativeDashboard()._render_svg_equity_chart(series)
    assert "<polyline" not in svg
    assert svg.count("<circle") >= 2
    assert "None" not in svg
