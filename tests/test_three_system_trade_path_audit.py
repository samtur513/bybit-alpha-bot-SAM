"""Read-only audit regressions; no network, trading runtime, or VPS services."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools import audit_three_system_trade_path as audit


class TradePathReadOnlyTest(unittest.TestCase):
    def test_closed_hour_catchup_exact(self):
        now=24*audit.HOUR + 4*60_000
        expected=23*audit.HOUR
        got=audit.latest_bar_report({"BTCUSDT":expected,"ETHUSDT":expected-audit.HOUR},now)
        self.assertEqual(got["symbols_seen"],2)
        self.assertEqual(got["symbols_caught_up"],1)
        self.assertEqual(got["missing_symbols"],["ETHUSDT"])
        self.assertFalse(got["publication_grace"])

    def test_near_boundary_grace(self):
        now=24*audit.HOUR + 15_000
        got=audit.latest_bar_report({"BTCUSDT":23*audit.HOUR},now)
        self.assertTrue(got["publication_grace"])
        self.assertEqual(got["symbols_caught_up"],1)

    def test_native_sqlite_is_read_only_and_counts_are_authoritative(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"native_ledger.db"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE native_state_journal (journal_id INTEGER PRIMARY KEY, state_json TEXT)")
                db.execute("CREATE TABLE native_dedup (event_key TEXT)")
                db.execute("CREATE TABLE native_journal (journal_id INTEGER PRIMARY KEY, iso_time TEXT)")
                db.execute("CREATE TABLE native_trades (trade_id TEXT)")
                db.execute("CREATE TABLE native_missed_opportunities (reason TEXT)")
                db.execute("INSERT INTO native_state_journal VALUES (?,?)",
                           (1,json.dumps({"engine":{"open_positions":{}},"range_engine":{"open_positions":{}},
                           "last_processed_bar_ts":{"BTCUSDT":23*audit.HOUR}})))
                db.execute("INSERT INTO native_dedup VALUES ('BTCUSDT_82800000')")
                db.execute("INSERT INTO native_journal VALUES (1,'2026-11-16T16:00:00Z')")
            before=path.read_bytes()
            with patch.object(audit,"DATA",Path(tmp)):
                out=audit.native_report(24*audit.HOUR+4*60_000)
            self.assertEqual(out["dedup_rows"],1)
            self.assertEqual(out["closed_trades"],0)
            self.assertEqual(out["bars"]["symbols_caught_up"],1)
            self.assertEqual(path.read_bytes(),before)

    def test_shadow_events_and_dedup_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/"shadow_ledger.db"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE shadow_dedup_events (symbol TEXT,exchange_ts INTEGER)")
                db.execute("CREATE TABLE shadow_execution_events (event_type TEXT, execution_status TEXT)")
                db.execute("CREATE TABLE shadow_state_journal (journal_id INTEGER PRIMARY KEY,state_json TEXT)")
                db.execute("INSERT INTO shadow_dedup_events VALUES ('BTCUSDT',?)",(23*audit.HOUR,))
                db.execute("INSERT INTO shadow_state_journal VALUES (1,?)",(json.dumps(
                    {"shadow_portfolio":{"positions":{},"closed_trades":[],
                    "rejected_orders_count":2}}),))
                db.execute("INSERT INTO shadow_execution_events VALUES ('ENTRY_REJECT','REJECTED')")
            before=path.read_bytes()
            with patch.object(audit,"DATA",Path(tmp)):
                out=audit.shadow_report(24*audit.HOUR+4*60_000)
            self.assertEqual(out["dedup_rows"],1)
            self.assertEqual(out["event_types"]["ENTRY_REJECT"],1)
            self.assertEqual(out["closed_trades_in_state"],0)
            self.assertEqual(path.read_bytes(),before)


if __name__=="__main__":
    unittest.main()
