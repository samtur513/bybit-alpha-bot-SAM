"""
Unit tests and invariant checks for Meta-Dashboard (Port 8085).
Verifies:
1. Aggregator configuration and engine definitions
2. Position normalization (dict format vs list format)
3. Total equity and PnL accounting invariants
4. News feed deduplication
5. Chart series generator
"""

import unittest
from unittest.mock import patch, MagicMock
import json

from meta_dashboard.config import ENGINES, META_PORT
from meta_dashboard.aggregator import MetaAggregator


class TestMetaDashboard(unittest.TestCase):
    def setUp(self):
        self.agg = MetaAggregator()

    def test_engines_configuration(self):
        self.assertEqual(len(ENGINES), 9)
        ports = [e["port"] for e in ENGINES]
        self.assertEqual(ports, [8080, 8081, 8082, 8083, 8084, 8086, 8087, 8088, 8089])
        self.assertEqual(META_PORT, 8085)

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
                    "is_pyramided": False,
                }
            },
            "stream_ready": True,
            "fresh_symbols": 25,
        }

        # Mock engine response for v3.2 (list trend & range open_positions)
        mock_data_v32 = {
            "portfolio_equity": 302.0,
            "initial_capital": 300.0,
            "trend_open_positions": [
                {
                    "symbol": "NEARUSDT",
                    "direction": "SHORT",
                    "qty": 30.0,
                    "entry_price": 4.0,
                    "current_price": 3.9,
                    "stop_loss": 4.2,
                    "unrealized_pnl": 3.0,
                }
            ],
            "range_open_positions": [],
            "range_pending_orders": [
                {
                    "symbol": "BEAMUSDT",
                    "direction": "BUY",
                    "price": 0.0045,
                    "qty": 10000.0,
                    "take_profit": 0.0048,
                    "stop_loss": 0.0042,
                }
            ],
            "stream_ready": True,
        }

        eng_v311 = [e for e in ENGINES if e["port"] == 8082][0]
        eng_v32 = [e for e in ENGINES if e["port"] == 8083][0]

        parsed_v311 = self._simulate_parse(eng_v311, mock_data_v311)
        self.assertEqual(len(parsed_v311["open_positions"]), 1)
        self.assertEqual(parsed_v311["open_positions"][0]["symbol"], "SOLUSDT")
        self.assertEqual(parsed_v311["open_positions"][0]["direction"], "SHORT")
        self.assertEqual(parsed_v311["open_positions"][0]["unrealized_pnl"], 5.0)

        parsed_v32 = self._simulate_parse(eng_v32, mock_data_v32)
        self.assertEqual(len(parsed_v32["open_positions"]), 1)
        self.assertEqual(parsed_v32["open_positions"][0]["symbol"], "NEARUSDT")
        self.assertEqual(len(parsed_v32["pending_orders"]), 1)
        self.assertEqual(parsed_v32["pending_orders"][0]["symbol"], "BEAMUSDT")

    def _simulate_parse(self, engine, data):
        # Helper to invoke the normalization logic with simulated response
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps(data).encode("utf-8")
        mock_response.__enter__.return_value = mock_response

        with patch("urllib.request.urlopen", return_value=mock_response):
            return self.agg._fetch_engine_status(engine)

    def test_accounting_invariants(self):
        # Populate latest states
        self.agg.latest_states = {
            "v20": {"capital": 300.0, "net_pnl": 0.0, "online": True, "open_positions": []},
            "v21": {"capital": 303.0, "net_pnl": 3.0, "online": True, "open_positions": [{"unrealized_pnl": 2.0}]},
            "v311": {"capital": 305.0, "net_pnl": 5.0, "online": True, "open_positions": [{"unrealized_pnl": 4.0}]},
            "v32": {"capital": 301.0, "net_pnl": 1.0, "online": True, "open_positions": []},
            "v314": {"capital": 300.0, "net_pnl": 0.0, "online": True, "open_positions": []},
            "v33": {"capital": 302.0, "net_pnl": 2.0, "online": True, "open_positions": []},
            "v34": {"capital": 300.0, "net_pnl": 0.0, "online": True, "open_positions": []},
            "v35": {"capital": 300.0, "net_pnl": 0.0, "online": True, "open_positions": []},
            "v36": {"capital": 300.0, "net_pnl": 0.0, "online": True, "open_positions": []},
        }

        snap = self.agg.get_snapshot()

        # Invariant 1: Total equity = sum of engine equities
        expected_total_eq = 300.0 + 303.0 + 305.0 + 301.0 + 300.0 + 302.0 + 300.0 + 300.0 + 300.0
        self.assertEqual(snap["total_combined_equity"], round(expected_total_eq, 2))

        # Invariant 2: Total realized pnl = sum of engine net pnls
        expected_realized = 0.0 + 3.0 + 5.0 + 1.0 + 0.0 + 2.0 + 0.0 + 0.0 + 0.0
        self.assertEqual(snap["total_realized_pnl"], round(expected_realized, 2))

        # Invariant 3: Total unrealized pnl = sum of position unrealized pnls
        self.assertEqual(snap["total_unrealized_pnl"], 6.0)

        # Invariant 4: Open positions count
        self.assertEqual(snap["total_open_positions"], 2)

    def test_news_deduplication(self):
        # Adding same headline twice
        h = "Bitcoin reaches new high after institutional inflow"
        item = {"timestamp": 123456, "headline": h, "symbol": "BTCUSDT", "impact": "MONITOR"}

        state1 = {"recent_news": [item], "capital": 300.0, "net_pnl": 0.0, "online": True, "open_positions": []}
        state2 = {"recent_news": [item], "capital": 300.0, "net_pnl": 0.0, "online": True, "open_positions": []}

        with patch.object(self.agg, "_fetch_engine_status", side_effect=[state1, state2, state1, state2, state1, state2, state1, state2, state1]):
            self.agg.update_all()

        # Invariant: news feed should only contain 1 item with that headline
        matching = [n for n in self.agg.master_news_feed if n["headline"] == h]
        self.assertEqual(len(matching), 1)

    def test_chart_data_structure(self):
        self.agg.timestamps_history.append("20:00:00")
        for eng in ENGINES:
            self.agg.equity_history[eng["id"]].append(300.0)

        chart = self.agg.get_chart_data()
        self.assertEqual(chart["labels"], ["20:00:00"])
        self.assertEqual(len(chart["datasets"]), 9)
        for ds in chart["datasets"]:
            self.assertIn("data", ds)
            self.assertIn("borderColor", ds)


if __name__ == "__main__":
    unittest.main()
