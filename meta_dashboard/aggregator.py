"""
Aggregator Module for Meta-Dashboard.
Periodically queries each of the 5 running engines via their /api/status endpoints,
tracks Bybit WebSocket and News Stream health, aggregates positions, orders, trades,
and maintains historical equity timeseries for real-time comparative charts.
"""

from collections import deque
from datetime import datetime, timezone
import json
import logging
import os
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional

from .config import ENGINES, POLL_INTERVAL_SECONDS

logger = logging.getLogger("MetaAggregator")


class MetaAggregator:
    def __init__(self, host: Optional[str] = None):
        self.host = host or os.environ.get("ENGINE_HOST", "127.0.0.1")
        self.poll_interval = POLL_INTERVAL_SECONDS
        self.running = False
        self.lock = threading.Lock()
        
        # State storage
        self.latest_states: Dict[str, Dict[str, Any]] = {}
        self.equity_history: Dict[str, deque] = {
            e["id"]: deque(maxlen=360) for e in ENGINES
        }
        self.timestamps_history: deque = deque(maxlen=360)
        self.last_chart_sample_ts = 0.0
        
        # News deduplicator
        self.seen_headlines = set()
        self.master_news_feed = deque(maxlen=50)

    def _fetch_engine_status(self, engine: Dict[str, Any]) -> Dict[str, Any]:
        port = engine["port"]
        url = f"http://{self.host}:{port}/api/status"
        t0 = time.time()
        
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "MetaDashboard/1.0", "Accept": "application/json"}
            )
            with urllib.request.urlopen(req, timeout=2.5) as response:
                latency_ms = round((time.time() - t0) * 1000.0, 1)
                data = json.loads(response.read().decode("utf-8"))
                
                # Normalize metrics
                capital = float(data.get("equity") or data.get("portfolio_equity") or data.get("capital") or 300.0)
                initial_capital = float(data.get("initial_capital", 300.0))
                peak_equity = float(data.get("peak_equity", capital))
                drawdown_pct = float(data.get("drawdown_pct", 0.0))
                net_pnl = round(capital - initial_capital, 2)
                return_pct = round((net_pnl / initial_capital) * 100.0, 2) if initial_capital > 0 else 0.0
                
                # Bybit Stream Health
                stream_ready = bool(data.get("stream_ready") or data.get("bybit_ws_connected") or data.get("bybit_connected") or ("radar" in data or "market_radar" in data))
                monitored_coins = int(data.get("monitored_symbols_count") or data.get("bybit_ws_symbols") or len(data.get("radar", {})) or len(data.get("market_radar", {})) or 18)
                fresh_symbols = int(data.get("fresh_symbols", monitored_coins))
                raw_btc = data.get("btc_trend_status") or data.get("btc_regime")
                btc_trend = str(raw_btc) if raw_btc else "DISABLED"
                next_candle = int(data.get("next_candle_seconds", 0))
                timeframe = str(data.get("timeframe", engine["timeframe"]))
                
                # News Core Health
                news_active = bool(data.get("news_defense_active") or data.get("news_shield") or data.get("news_core_status") == "v1.2 Shield")
                news_profile = str(data.get("news_profile", "v1.2 Shield" if news_active else "disabled"))
                recent_news = data.get("news_recent_events", [])
                
                # Positions & Orders Extraction
                normalized_positions = []
                
                # Format 1: dict or list open_positions (v2.0, v2.1, v3.1.1, v3.1.4, v3.2, v3.3, v3.4, v3.5, v3.6, v3.7)
                raw_open_pos = data.get("open_positions")
                if isinstance(raw_open_pos, dict):
                    for sym, pos in raw_open_pos.items():
                        entry_p = float(pos.get("entry_price", 0.0))
                        curr_p = float(pos.get("current_price", entry_p))
                        dir_str = str(pos.get("direction", "LONG")).upper()
                        unr_pnl = float(pos.get("unrealized_pnl", pos.get("floating_pnl", 0.0)))
                        unr_pct = round(((curr_p - entry_p) / entry_p * 100.0) if dir_str == "LONG" else ((entry_p - curr_p) / entry_p * 100.0), 2) if entry_p > 0 else 0.0
                        normalized_positions.append({
                            "engine_id": engine["id"],
                            "engine_name": engine["name"],
                            "port": port,
                            "symbol": sym,
                            "direction": dir_str,
                            "module": pos.get("module", "ALPHA_PAIRS" if engine["id"] == "v35" else "TREND"),
                            "qty": float(pos.get("qty", 0.0)),
                            "entry_price": entry_p,
                            "current_price": curr_p,
                            "stop_loss": float(pos.get("current_stop", pos.get("stop_loss", pos.get("stop_price", 0.0)))),
                            "take_profit": float(pos.get("take_profit", 0.0)) if pos.get("take_profit") else None,
                            "unrealized_pnl": unr_pnl,
                            "unrealized_pct": unr_pct,
                            "is_pyramided": bool(pos.get("is_pyramided", False)),
                            "is_breakeven_set": bool(pos.get("is_breakeven_set", False)),
                        })
                elif isinstance(raw_open_pos, list) and len(raw_open_pos) > 0:
                    for pos in raw_open_pos:
                        sym = pos.get("symbol", "")
                        entry_p = float(pos.get("entry_price", 0.0))
                        curr_p = float(pos.get("current_price", entry_p))
                        dir_str = str(pos.get("direction", "LONG")).upper()
                        unr_pnl = float(pos.get("unrealized_pnl", pos.get("floating_pnl", 0.0)))
                        unr_pct = round(((curr_p - entry_p) / entry_p * 100.0) if dir_str == "LONG" else ((entry_p - curr_p) / entry_p * 100.0), 2) if entry_p > 0 else 0.0
                        normalized_positions.append({
                            "engine_id": engine["id"],
                            "engine_name": engine["name"],
                            "port": port,
                            "symbol": sym,
                            "direction": dir_str,
                            "module": pos.get("module", "ALPHA_PAIRS" if engine["id"] == "v35" else "TREND"),
                            "qty": float(pos.get("qty", 0.0)),
                            "entry_price": entry_p,
                            "current_price": curr_p,
                            "stop_loss": float(pos.get("current_stop", pos.get("stop_loss", pos.get("stop_price", 0.0)))),
                            "take_profit": float(pos.get("take_profit", 0.0)) if pos.get("take_profit") else None,
                            "unrealized_pnl": unr_pnl,
                            "unrealized_pct": unr_pct,
                            "is_pyramided": bool(pos.get("is_pyramided", False)),
                            "is_breakeven_set": bool(pos.get("is_breakeven_set", False)),
                        })
                else:
                    # Fallback Format 2: list trend_open_positions (v3.2)
                    if isinstance(data.get("trend_open_positions"), list):
                        for pos in data["trend_open_positions"]:
                            entry_p = float(pos.get("entry_price", 0.0))
                            curr_p = float(pos.get("current_price", entry_p))
                            dir_str = str(pos.get("direction", "LONG")).upper()
                            unr_pnl = float(pos.get("unrealized_pnl", 0.0))
                            unr_pct = round(((curr_p - entry_p) / entry_p * 100.0) if dir_str == "LONG" else ((entry_p - curr_p) / entry_p * 100.0), 2) if entry_p > 0 else 0.0
                            normalized_positions.append({
                                "engine_id": engine["id"],
                                "engine_name": engine["name"],
                                "port": port,
                                "symbol": pos.get("symbol", ""),
                                "direction": dir_str,
                                "module": "TREND",
                                "qty": float(pos.get("qty", 0.0)),
                                "entry_price": entry_p,
                                "current_price": curr_p,
                                "stop_loss": float(pos.get("stop_loss", 0.0)),
                                "take_profit": None,
                                "unrealized_pnl": unr_pnl,
                                "unrealized_pct": unr_pct,
                                "is_pyramided": bool(pos.get("is_pyramided", False)),
                                "is_breakeven_set": False,
                            })

                    # Fallback Format 3: list range_open_positions (v3.2)
                    if isinstance(data.get("range_open_positions"), list):
                        for pos in data["range_open_positions"]:
                            entry_p = float(pos.get("entry_price", 0.0))
                            curr_p = float(pos.get("current_price", entry_p))
                            dir_str = str(pos.get("direction", "LONG")).upper()
                            unr_pnl = float(pos.get("unrealized_pnl", 0.0))
                            unr_pct = round(((curr_p - entry_p) / entry_p * 100.0) if dir_str == "LONG" else ((entry_p - curr_p) / entry_p * 100.0), 2) if entry_p > 0 else 0.0
                            normalized_positions.append({
                                "engine_id": engine["id"],
                                "engine_name": engine["name"],
                                "port": port,
                                "symbol": pos.get("symbol", ""),
                                "direction": dir_str,
                                "module": "RANGE_MAKER",
                                "qty": float(pos.get("qty", 0.0)),
                                "entry_price": entry_p,
                                "current_price": curr_p,
                                "stop_loss": float(pos.get("stop_loss", 0.0)),
                                "take_profit": float(pos.get("take_profit", 0.0)) if pos.get("take_profit") else None,
                                "unrealized_pnl": unr_pnl,
                                "unrealized_pct": unr_pct,
                                "is_pyramided": False,
                                "is_breakeven_set": False,
                            })

                # Deduplicate by (symbol, direction, module)
                deduped = []
                seen_pos = set()
                for p in normalized_positions:
                    key = (p["symbol"], p["direction"], p["module"])
                    if key not in seen_pos:
                        seen_pos.add(key)
                        deduped.append(p)
                normalized_positions = deduped

                # Pending Orders (v3.2)
                normalized_orders = []
                if isinstance(data.get("range_pending_orders"), list):
                    for o in data["range_pending_orders"]:
                        normalized_orders.append({
                            "engine_id": engine["id"],
                            "engine_name": engine["name"],
                            "port": port,
                            "symbol": o.get("symbol", ""),
                            "direction": o.get("direction", ""),
                            "price": float(o.get("price", 0.0)),
                            "qty": float(o.get("qty", 0.0)),
                            "take_profit": float(o.get("take_profit", 0.0)),
                            "stop_loss": float(o.get("stop_loss", 0.0)),
                            "order_type": "LIMIT_MAKER",
                        })

                # Closed trades
                raw_closed = data.get("closed_trades", [])
                closed_trades = []
                if isinstance(raw_closed, list):
                    for t in raw_closed:
                        pnl_val = t.get("net_pnl", t.get("pnl", 0.0))
                        try:
                            t_net_pnl = float(pnl_val) if pnl_val is not None else 0.0
                        except (ValueError, TypeError):
                            t_net_pnl = 0.0

                        entry_p = float(t.get("entry_price", 0.0))
                        exit_p = float(t.get("exit_price", 0.0))
                        reason = str(t.get("exit_reason", t.get("reason", "EXIT"))).upper()

                        closed_trades.append({
                            "engine_id": engine["id"],
                            "engine_name": engine["name"],
                            "port": port,
                            "symbol": t.get("symbol", ""),
                            "direction": str(t.get("direction", "LONG")).upper(),
                            "entry_price": entry_p,
                            "exit_price": exit_p,
                            "net_pnl": round(t_net_pnl, 2),
                            "hold_hours": t.get("hold_hours", 0),
                            "reason": reason,
                            "was_pyramided": bool(t.get("was_pyramided", False)),
                        })
                
                # Gearbox
                gear = data.get("gearbox_gear", 1)
                risk_pct = data.get("gearbox_risk_pct", 1.5)

                return {
                    "id": engine["id"],
                    "name": engine["name"],
                    "tag": engine["tag"],
                    "port": port,
                    "color": engine["color"],
                    "strategy": engine["strategy"],
                    "timeframe": timeframe,
                    "online": True,
                    "latency_ms": latency_ms,
                    "bybit_connected": stream_ready,
                    "candles_ready": fresh_symbols,
                    "monitored_coins": monitored_coins,
                    "btc_trend": btc_trend,
                    "next_candle_sec": next_candle,
                    "news_defense_active": news_active,
                    "news_profile": news_profile,
                    "news_recent_count": len(recent_news),
                    "capital": capital,
                    "initial_capital": initial_capital,
                    "net_pnl": net_pnl,
                    "return_pct": return_pct,
                    "drawdown_pct": drawdown_pct,
                    "peak_equity": peak_equity,
                    "gear": gear,
                    "risk_pct": risk_pct,
                    "open_positions": normalized_positions,
                    "pending_orders": normalized_orders,
                    "closed_trades": closed_trades,
                    "recent_news": recent_news,
                    "last_updated": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
                }
        except Exception as e:
            return {
                "id": engine["id"],
                "name": engine["name"],
                "tag": engine["tag"],
                "port": port,
                "color": engine["color"],
                "strategy": engine["strategy"],
                "timeframe": engine["timeframe"],
                "online": False,
                "latency_ms": -1,
                "error": str(e),
                "bybit_connected": False,
                "news_defense_active": False,
                "capital": 300.0,
                "net_pnl": 0.0,
                "return_pct": 0.0,
                "drawdown_pct": 0.0,
                "open_positions": [],
                "pending_orders": [],
                "closed_trades": [],
                "recent_news": [],
                "last_updated": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
            }

    def update_all(self):
        new_states = {}
        for eng in ENGINES:
            st = self._fetch_engine_status(eng)
            new_states[eng["id"]] = st

            # Merge news into master feed
            for n in st.get("recent_news", []):
                h = n.get("headline", "")
                if h and h not in self.seen_headlines:
                    self.seen_headlines.add(h)
                    self.master_news_feed.appendleft({
                        "timestamp": n.get("timestamp", int(time.time() * 1000)),
                        "headline": h,
                        "symbol": n.get("symbol", "MARKET"),
                        "event_type": n.get("event_type", "INFO"),
                        "impact": n.get("impact", "MONITOR"),
                        "source_engine": eng["name"],
                    })

        # Chart sampling (every 30 seconds -> 360 points = 3 hours rolling window)
        now = time.time()
        with self.lock:
            self.latest_states = new_states
            if now - self.last_chart_sample_ts >= 30.0:
                self.last_chart_sample_ts = now
                dt_str = datetime.now(timezone.utc).strftime("%H:%M:%S")
                self.timestamps_history.append(dt_str)
                for eng in ENGINES:
                    eq = new_states.get(eng["id"], {}).get("capital", 300.0)
                    self.equity_history[eng["id"]].append(eq)

    def run_loop(self):
        self.running = True
        logger.info(f"MetaAggregator background worker started (target: {self.host}, interval: {self.poll_interval}s)")
        while self.running:
            try:
                self.update_all()
            except Exception as e:
                logger.error(f"Error in MetaAggregator update loop: {e}")
            time.sleep(self.poll_interval)

    def get_snapshot(self) -> Dict[str, Any]:
        with self.lock:
            states_list = [self.latest_states.get(e["id"], {}) for e in ENGINES]
            
            # Combine all open positions
            all_positions = []
            all_orders = []
            all_trades = []
            total_equity = 0.0
            total_net_pnl = 0.0
            total_unrealized_pnl = 0.0
            online_count = 0
            
            for st in states_list:
                if st.get("online"):
                    online_count += 1
                total_equity += st.get("capital", 300.0)
                total_net_pnl += st.get("net_pnl", 0.0)
                pos_list = st.get("open_positions", [])
                all_positions.extend(pos_list)
                all_orders.extend(st.get("pending_orders", []))
                all_trades.extend(st.get("closed_trades", []))
                for p in pos_list:
                    total_unrealized_pnl += p.get("unrealized_pnl", 0.0)

            return {
                "timestamp_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                "online_engines": f"{online_count} / {len(ENGINES)} Active",
                "total_combined_equity": round(total_equity, 2),
                "total_realized_pnl": round(total_net_pnl, 2),
                "total_unrealized_pnl": round(total_unrealized_pnl, 2),
                "total_open_positions": len(all_positions),
                "total_pending_orders": len(all_orders),
                "engines": states_list,
                "all_open_positions": all_positions,
                "all_pending_orders": all_orders,
                "all_closed_trades": all_trades,
                "master_news_feed": list(self.master_news_feed)[:30],
            }

    def get_chart_data(self) -> Dict[str, Any]:
        with self.lock:
            labels = list(self.timestamps_history)
            datasets = []
            for eng in ENGINES:
                datasets.append({
                    "id": eng["id"],
                    "label": f"{eng['name']} ({eng['port']})",
                    "borderColor": eng["color"],
                    "backgroundColor": eng["color"] + "1a",
                    "data": list(self.equity_history[eng["id"]]),
                    "fill": False,
                    "tension": 0.2,
                })
            return {"labels": labels, "datasets": datasets}
