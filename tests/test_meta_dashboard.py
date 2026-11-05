"""
Unit & Integration Tests for Meta-Dashboard (Port 8085).
Verifies aggregation, normalization of varied engine formats, accounting invariants,
and real-time chart data consistency across all active engines.
"""

from decimal import Decimal
import unittest
from unittest.mock import MagicMock, patch

from meta_dashboard.config import ENGINES, META_PORT
from meta_dashboard.aggregator import MetaAggregator


class TestMetaDashboard(unittest.TestCase):
    def setUp(self):
        self.agg = MetaAggregator()

    def test_engines_configuration(self):
        self.assertEqual(len(ENGINES), 16)
        ports = [e["port"] for e in ENGINES]
        self.assertIn(8080, ports)
        self.assertIn(8094, ports)
        self.assertEqual(META_PORT, 8085)
        # Check v3.13 configuration on port 8080
        e8080 = next(e for e in ENGINES if e["port"] == 8080)
        self.assertEqual(e8080["id"], "v313")
        self.assertEqual(e8080["name"], "v3.13 Sector Arb")
        # Check v3.14 configuration on port 8094
        e8094 = next(e for e in ENGINES if e["port"] == 8094)
        self.assertEqual(e8094["id"], "v314_dispersion")
        self.assertEqual(e8094["name"], "v3.14 Basket Dispersion")

    def test_position_normalization_dict_and_list(self):
        # Mock engine response for v3.1.1 (dict open_positions)
        mock_data_v311 = {
            "equity": 305.50,
            "initial_capital": 300.0,
            "open_positions": {
                "SOLUSDT": {
                    "direction": "SHORT",
                    "qty": 2.5,
                    "entry_price": 90.0,
                    "current_price": 88.0,
                    "current_stop": 92.0,
                    "unrealized_pnl": 5.0,
                }
            },
        }

        # Mock engine response for v3.2 (list open_positions)
        mock_data_v32 = {
            "equity": 298.20,
            "initial_capital": 300.0,
            "open_positions": [
                {
                    "symbol": "BTCUSDT",
                    "direction": "LONG",
                    "qty": 0.05,
                    "entry_price": 60000.0,
                    "current_price": 60500.0,
                    "stop_loss": 59500.0,
                    "unrealized_pnl": 25.0,
                }
            ],
        }

        engine_v311 = next(e for e in ENGINES if e["id"] == "v311")
        engine_v32 = next(e for e in ENGINES if e["id"] == "v32")

        with patch("urllib.request.urlopen") as mock_urlopen:
            # 1. Test dict format
            mock_resp = MagicMock()
            mock_resp.read.return_value = json_bytes(mock_data_v311)
            mock_urlopen.return_value.__enter__.return_value = mock_resp
            st1 = self.agg._fetch_engine_status(engine_v311)
            self.assertEqual(len(st1["open_positions"]), 1)
            self.assertEqual(st1["open_positions"][0]["symbol"], "SOLUSDT")
            self.assertEqual(st1["open_positions"][0]["direction"], "SHORT")
            self.assertEqual(st1["open_positions"][0]["unrealized_pnl"], 5.0)

            # 2. Test list format
            mock_resp.read.return_value = json_bytes(mock_data_v32)
            st2 = self.agg._fetch_engine_status(engine_v32)
            self.assertEqual(len(st2["open_positions"]), 1)
            self.assertEqual(st2["open_positions"][0]["symbol"], "BTCUSDT")
            self.assertEqual(st2["open_positions"][0]["direction"], "LONG")
            self.assertEqual(st2["open_positions"][0]["unrealized_pnl"], 25.0)

    def test_offline_engine_graceful_handling(self):
        engine = ENGINES[0]
        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            st = self.agg._fetch_engine_status(engine)
            self.assertFalse(st["online"])
            self.assertEqual(st["capital"], 300.0)
            self.assertEqual(st["open_positions"], [])
            self.assertFalse(st["bybit_connected"])

    def test_accounting_invariants(self):
        # Populate latest states for all engines
        self.agg.latest_states = {
            e["id"]: {"capital": 300.0, "net_pnl": 0.0, "online": True, "open_positions": []}
            for e in ENGINES
        }
        self.agg.latest_states["v21"]["capital"] = 303.0
        self.agg.latest_states["v21"]["net_pnl"] = 3.0
        self.agg.latest_states["v21"]["open_positions"] = [{"unrealized_pnl": 2.0}]

        self.agg.latest_states["v311"]["capital"] = 305.0
        self.agg.latest_states["v311"]["net_pnl"] = 5.0
        self.agg.latest_states["v311"]["open_positions"] = [{"unrealized_pnl": 4.0}]

        snap = self.agg.get_snapshot()

        # Invariant 1: Total equity = sum of engine equities
        expected_total_eq = sum(st["capital"] for st in self.agg.latest_states.values())
        self.assertEqual(snap["total_combined_equity"], round(expected_total_eq, 2))

        # Invariant 2: Total realized pnl = sum of engine net pnls
        expected_realized = sum(st["net_pnl"] for st in self.agg.latest_states.values())
        self.assertEqual(snap["total_realized_pnl"], round(expected_realized, 2))

        # Invariant 3: Total unrealized pnl = sum of position unrealized pnls
        self.assertEqual(snap["total_unrealized_pnl"], 6.0)

        # Invariant 4: Open positions count
        self.assertEqual(snap["total_open_positions"], 2)

    def test_news_deduplication(self):
        h = "Bitcoin reaches new high after institutional inflow"
        item = {"timestamp": 123456, "headline": h, "symbol": "BTCUSDT", "impact": "MONITOR"}

        state1 = {"recent_news": [item], "capital": 300.0, "net_pnl": 0.0, "online": True, "open_positions": []}
        state2 = {"recent_news": [item], "capital": 300.0, "net_pnl": 0.0, "online": True, "open_positions": []}

        side_effects = [state1, state2] * (len(ENGINES) + 1)
        with patch.object(self.agg, "_fetch_engine_status", side_effect=side_effects):
            self.agg.update_all()

        matching = [n for n in self.agg.master_news_feed if n["headline"] == h]
        self.assertEqual(len(matching), 1)

    def test_chart_data_structure(self):
        self.agg.timestamps_history.append("20:00:00")
        for eng in ENGINES:
            self.agg.equity_history[eng["id"]].append(300.0)

        chart = self.agg.get_chart_data()
        self.assertEqual(chart["labels"], ["20:00:00"])
        self.assertEqual(len(chart["datasets"]), len(ENGINES))
        for ds in chart["datasets"]:
            self.assertIn("data", ds)
            self.assertIn("borderColor", ds)


def json_bytes(obj):
    import json
    return json.dumps(obj).encode("utf-8")


if __name__ == "__main__":
    unittest.main()
