"""Independent regression tests for evidence-safe Dashboard 8085 hotfix."""
import json
import sqlite3

from v3_2.src.integrations.trading_control_center_data import TradingControlCenterDataEngine
from v3_2.src.integrations.three_system_dashboard import ThreeSystemComparativeDashboard


def _shadow_db(path, snapshots):
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE shadow_state_journal ("
            "journal_id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "saved_at_utc TEXT, shadow_realized_equity REAL, state_json TEXT)"
        )
        for timestamp, equity, state in snapshots:
            conn.execute(
                "INSERT INTO shadow_state_journal "
                "(saved_at_utc, shadow_realized_equity, state_json) VALUES (?, ?, ?)",
                (timestamp, equity, json.dumps(state)),
            )


def test_unpriced_shadow_open_position_creates_a_gap_not_zero_pnl(tmp_path):
    db = tmp_path / "shadow.sqlite"
    state_empty = {"shadow_portfolio": {"positions": {}, "closed_trades": []}}
    state_unpriced = {
        "shadow_portfolio": {
            "positions": {
                "BTCUSDT": {
                    "direction": 1,
                    "entry_price": "50000",
                    "current_qty": "0.1",
                    # No market price, no recorded unrealized PnL.
                }
            },
            "closed_trades": [],
        }
    }
    _shadow_db(
        db,
        [
            ("2026-11-16T10:00:00Z", 300.0, state_empty),
            ("2026-11-16T11:00:00Z", 300.0, state_unpriced),
        ],
    )
    data = TradingControlCenterDataEngine(shadow_ledger_path=db).get_analytics_data()
    history = data["shadow"]["equity_history"]
    assert len(history) == 2
    assert history[0]["equity"] == 300.0
    assert history[1]["equity"] is None
    assert history[1]["valuation_status"] == "UNVALUED_OR_STALE"
    assert history[1]["unrealized_pnl"] is None
    svg = ThreeSystemComparativeDashboard()._render_svg_equity_chart(history)
    assert "UNVALUED_OR_STALE" in svg


def test_known_shadow_mark_makes_real_mtm(tmp_path):
    db = tmp_path / "shadow.sqlite"
    state = {
        "shadow_portfolio": {
            "positions": {
                "BTCUSDT": {
                    "direction": 1, "entry_price": "100",
                    "current_qty": "1", "current_mark": "88",
                }
            },
            "closed_trades": [],
        }
    }
    _shadow_db(db, [("2026-11-16T11:00:00Z", 300, state)])
    history = TradingControlCenterDataEngine(shadow_ledger_path=db).get_analytics_data()["shadow"]["equity_history"]
    assert history[0]["equity"] == 288.0
    assert history[0]["unrealized_pnl"] == -12.0


def test_empty_r2_db_cannot_fabricate_300_dollar_account(tmp_path):
    db = tmp_path / "observer.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE accounts(id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
    telemetry = tmp_path / "telemetry.json"
    telemetry.write_text(json.dumps({
        "heartbeat_at": "2026-11-16T11:00:00Z",
        "profiles": [{"profile": "BASELINE:300:BASE:OBSERVED_BOOK", "cash_usdt": 300}],
    }))
    engine = TradingControlCenterDataEngine(r2_ledger_path=db, r2_telemetry_path=telemetry)
    account, _, status, _ = engine._get_r2_data()
    assert account == {}
    assert status == "DATA_SOURCE_ERROR"


def test_reconciliation_never_auto_certifies_by_mtm_balance():
    engine = TradingControlCenterDataEngine()
    trade = [{"net_pnl_usdt": 15.3}]
    status = engine.compute_accounting_reconciliation("NATIVE", 300.0, 315.3, trade)
    assert status["status"] == "ACCOUNTING_PENDING_RECONCILIATION"
    assert status["reconciled"] is False
    assert status["discrepancy_usdt"] is None
