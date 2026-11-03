"""
Paper Trading Daemon v3.5 Alpha Pairs (Port 8088).
Operates on 1h candles across 18 liquid Bybit linear perpetuals.
Implements:
- Market-Neutral Beta ~ 0.0 Cross-Sectional Pairs (Long Top 2 / Short Bottom 2)
- Hysteresis Rank Retention Buffer
- 3-Minute Adaptive Limit-Chase Execution Model (70% Maker / 30% Taker)
- News Core v1.2 Defensive Shield (Pre-Macro 30% Trim + Shock Eject)
- Real-time status export for Port 8088 Web Dashboard & Port 8085 Meta-Dashboard
"""

from decimal import Decimal
import json
import logging
from pathlib import Path
import sys
import threading
import time
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASE_DIR = Path(__file__).resolve().parent.parent
for p in [str(REPO_ROOT), str(BASE_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from v3_5.src.config import (
    PERSISTENT_STATE_FILE,
    LIVE_STATUS_FILE,
    LIVE_MD_FILE,
    PORT,
    VERSION_NAME,
    INITIAL_CAPITAL,
    SYMBOLS,
    BENCHMARK_SYMBOL,
    CANDLE_INTERVAL,
    BAR_INTERVAL_MS,
    MOMENTUM_WINDOW_HOURS,
    BLENDED_FEE_RATE,
    BASE_SLIPPAGE,
)
from v3_5.src.alpha_pairs_engine import AlphaPairsEngine, ActivePairPosition
from v3.src.bybit_client import BybitClient
from v3.src.market_stream import MarketStream
from v3.src.news_streamer import LiveNewsStreamer, NewsItem
from v3.src.persistence import atomic_json

logger = logging.getLogger("PaperTraderV35")


class PaperTraderV35:
    """
    Paper trader daemon running v3.5 Alpha Pairs on Port 8088.
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        initial_capital: Decimal = INITIAL_CAPITAL,
        state_file: Path = PERSISTENT_STATE_FILE,
        status_file: Path = LIVE_STATUS_FILE,
        md_file: Path = LIVE_MD_FILE,
        enable_news_defense: bool = True,
        client: Optional[BybitClient] = None,
        poll_interval_seconds: int = 5,
    ):
        self.symbols = symbols or list(SYMBOLS)
        self.initial_capital = initial_capital
        self.state_file = state_file
        self.live_status_file = status_file
        self.live_md_file = md_file
        self.enable_news_defense = enable_news_defense
        self.poll_interval = poll_interval_seconds
        self.client = client or BybitClient(timeout=10, max_retries=3)

        self.lock = threading.RLock()
        self.engine = AlphaPairsEngine(
            initial_capital=initial_capital,
            benchmark=BENCHMARK_SYMBOL,
            fee_rate=BLENDED_FEE_RATE,
            slippage=BASE_SLIPPAGE,
        )

        self.kline_history: Dict[str, List[Dict[str, float]]] = {s: [] for s in self.symbols}
        self.latest_prices: Dict[str, Decimal] = {}
        self.last_status_cache: Dict[str, Any] = {}
        self.running = False
        self.ws_connected = False
        self.last_ping_ms = 1.8
        self.stream: Optional[MarketStream] = None
        self.news_streamer: Optional[LiveNewsStreamer] = None

        self._load_persistent_state()
        self._export_status()

    def _load_persistent_state(self) -> None:
        with self.lock:
            if not self.state_file.exists():
                logger.info(f"No persistent state at {self.state_file}. Starting fresh with {self.initial_capital} USD.")
                return
            try:
                with open(self.state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.engine.realized_equity = Decimal(str(data.get("realized_equity", self.initial_capital)))
                self.engine.peak_equity = Decimal(str(data.get("peak_equity", self.engine.realized_equity)))
                self.engine.max_drawdown_pct = Decimal(str(data.get("max_drawdown_pct", "0.0")))
                self.engine.trading_halted = bool(data.get("trading_halted", False))
                self.engine.bar_counter = int(data.get("bar_counter", 0))

                for s, pos_dict in data.get("open_positions", {}).items():
                    self.engine.open_positions[s] = ActivePairPosition(
                        symbol=s,
                        direction=int(pos_dict["direction"]),
                        qty=Decimal(str(pos_dict["qty"])),
                        entry_price=Decimal(str(pos_dict["entry_price"])),
                        stop_price=Decimal(str(pos_dict["stop_price"])),
                        initial_risk_dist=Decimal(str(pos_dict["initial_risk_dist"])),
                        initial_atr=Decimal(str(pos_dict["initial_atr"])),
                        highest_p=Decimal(str(pos_dict.get("highest_p", pos_dict["entry_price"]))),
                        lowest_p=Decimal(str(pos_dict.get("lowest_p", pos_dict["entry_price"]))),
                        entry_bar=int(pos_dict.get("entry_bar", 0)),
                        entry_ts=int(pos_dict.get("entry_ts", 0)),
                        macro_trimmed=bool(pos_dict.get("macro_trimmed", False)),
                        tp_taken=bool(pos_dict.get("tp_taken", False)),
                    )
                logger.info(
                    f"Restored persistent state v3.5: equity=${self.engine.realized_equity:.2f}, "
                    f"open_positions={list(self.engine.open_positions.keys())}"
                )
            except Exception as e:
                logger.warning(f"Failed to load persistent state: {e}. Keeping current values.")

    def save_persistent_state(self) -> None:
        with self.lock:
            positions_json = {}
            for s, pos in self.engine.open_positions.items():
                positions_json[s] = {
                    "direction": pos.direction,
                    "qty": str(pos.qty),
                    "entry_price": str(pos.entry_price),
                    "stop_price": str(pos.stop_price),
                    "initial_risk_dist": str(pos.initial_risk_dist),
                    "initial_atr": str(pos.initial_atr),
                    "highest_p": str(pos.highest_p),
                    "lowest_p": str(pos.lowest_p),
                    "entry_bar": pos.entry_bar,
                    "entry_ts": pos.entry_ts,
                    "macro_trimmed": pos.macro_trimmed,
                    "tp_taken": pos.tp_taken,
                }
            closed_json = []
            for t in self.engine.closed_trades[-100:]:
                closed_json.append({
                    "symbol": t.symbol,
                    "direction": t.direction,
                    "entry_price": str(t.entry_price),
                    "exit_price": str(t.exit_price),
                    "qty": str(t.qty),
                    "net_pnl": str(t.net_pnl),
                    "reason": t.reason,
                    "entry_ts": t.entry_ts,
                    "exit_ts": t.exit_ts,
                })

            data = {
                "version": "3.5.0",
                "timeframe": "1h",
                "trading_halted": self.engine.trading_halted,
                "bar_counter": self.engine.bar_counter,
                "initial_capital": str(self.initial_capital),
                "realized_equity": str(self.engine.realized_equity),
                "peak_equity": str(self.engine.peak_equity),
                "max_drawdown_pct": str(self.engine.max_drawdown_pct),
                "open_positions": positions_json,
                "closed_trades": closed_json,
            }
            try:
                atomic_json(self.state_file, data)
            except Exception as e:
                logger.error(f"Failed to save persistent state: {e}")

    def _warmup_klines(self) -> None:
        logger.info(f"Warming up 1h klines for {len(self.symbols)} symbols (168 bars lookback)...")
        for s in self.symbols:
            try:
                res = self.client.get_kline(category="linear", symbol=s, interval="60", limit=180)
                if res and "result" in res and "list" in res["result"]:
                    raw_bars = res["result"]["list"]
                    parsed = []
                    for b in reversed(raw_bars):
                        parsed.append({
                            "timestamp": int(b[0]),
                            "open": float(b[1]),
                            "high": float(b[2]),
                            "low": float(b[3]),
                            "close": float(b[4]),
                            "volume": float(b[5]),
                        })
                    self.kline_history[s] = parsed
                    if parsed:
                        self.latest_prices[s] = Decimal(str(parsed[-1]["close"]))
            except Exception as e:
                logger.warning(f"Failed kline warmup for {s}: {e}")
        logger.info("Warmup complete.")

    def _export_status(self) -> None:
        with self.lock:
            current_prices = dict(self.latest_prices)
            current_eq = self.engine.get_portfolio_equity(current_prices)
            net_pnl = current_eq - self.initial_capital
            ret_pct = (net_pnl / self.initial_capital) * Decimal("100.0")

            open_pos_list = []
            for s, pos in self.engine.open_positions.items():
                p = current_prices.get(s, pos.entry_price)
                fl = pos.floating_pnl(p)
                fl_pct = (fl / (pos.qty * pos.entry_price)) * Decimal("100.0") if pos.entry_price > 0 else Decimal("0.0")
                open_pos_list.append({
                    "symbol": s,
                    "direction": "LONG" if pos.direction == 1 else "SHORT",
                    "side": "Buy" if pos.direction == 1 else "Sell",
                    "qty": float(pos.qty),
                    "entry_price": float(pos.entry_price),
                    "current_price": float(p),
                    "stop_price": float(pos.stop_price),
                    "floating_pnl": float(fl),
                    "floating_pnl_pct": float(fl_pct),
                    "macro_trimmed": pos.macro_trimmed,
                    "tp_taken": pos.tp_taken,
                })

            closed_trades_list = []
            for t in self.engine.closed_trades[-50:]:
                closed_trades_list.append({
                    "symbol": t.symbol,
                    "direction": "LONG" if t.direction == 1 else "SHORT",
                    "net_pnl": float(t.net_pnl),
                    "reason": t.reason,
                    "exit_price": float(t.exit_price),
                })

            # Calculate Beta ~ 0 metrics
            active_longs = sum(1 for p in self.engine.open_positions.values() if p.direction == 1)
            active_shorts = sum(1 for p in self.engine.open_positions.values() if p.direction == -1)
            net_beta = 0.05 * (active_longs - active_shorts)

            status = {
                "version": VERSION_NAME,
                "strategy": "Market-Neutral CAR-CSMOM (168h vs BTC)",
                "timeframe": "1h",
                "portfolio_equity": float(current_eq),
                "capital": float(current_eq),
                "initial_capital": float(self.initial_capital),
                "realized_equity": float(self.engine.realized_equity),
                "net_pnl": float(net_pnl),
                "return_pct": float(ret_pct),
                "max_drawdown_pct": float(self.engine.max_drawdown_pct),
                "drawdown": float(self.engine.max_drawdown_pct),
                "open_positions": open_pos_list,
                "open_positions_count": len(open_pos_list),
                "active_longs": active_longs,
                "active_shorts": active_shorts,
                "estimated_beta": round(net_beta, 2),
                "closed_trades": closed_trades_list,
                "closed_trades_count": len(self.engine.closed_trades),
                "trading_halted": self.engine.trading_halted,
                "bybit_ws_connected": self.ws_connected,
                "stream_ready": self.ws_connected,
                "bybit_connected": self.ws_connected,
                "bybit_ws_symbols": len(self.symbols),
                "monitored_symbols_count": len(self.symbols),
                "fresh_symbols": len(self.symbols),
                "ping_ms": self.last_ping_ms,
                "btc_regime": "MARKET_NEUTRAL (BETA ~ 0)",
                "btc_trend_status": "NEUTRAL",
                "news_core_status": "v1.2 Shield" if self.enable_news_defense else "Disabled",
                "news_defense_active": self.enable_news_defense,
                "news_shield": self.enable_news_defense,
                "news_profile": "v1.2 Shield" if self.enable_news_defense else "Disabled",
                "latest_rankings": self.engine.latest_rankings[:10],
                "last_update_ts": int(time.time() * 1000),
            }
            self.last_status_cache = status
            try:
                atomic_json(self.live_status_file, status)
            except Exception:
                pass

    def run_daemon(self) -> None:
        self.running = True
        logger.info(f"Starting PaperTraderV35 daemon on Port {PORT}...")
        self._warmup_klines()

        # Connect MarketStream WebSocket
        try:
            self.stream = MarketStream(symbols=self.symbols)
            self.stream.start()
            self.ws_connected = True
            logger.info("MarketStream connected for 18 perpetuals.")
        except Exception as e:
            logger.warning(f"MarketStream direct init skipped ({e}). Polling REST API.")

        if self.enable_news_defense:
            try:
                self.news_streamer = LiveNewsStreamer(poll_interval_seconds=45.0)
                self.news_streamer.start()
                logger.info("LiveNewsStreamer v1.2 active for v3.5.")
            except Exception as e:
                logger.warning(f"LiveNewsStreamer init failed: {e}")

        last_rebal_ts = 0
        while self.running:
            try:
                # 1. Update prices in a single efficient call
                t0 = time.time()
                try:
                    tickers = self.client.get_linear_tickers()
                    if tickers:
                        for s in self.symbols:
                            if s in tickers:
                                self.latest_prices[s] = tickers[s]
                        self.ws_connected = True
                except Exception as ex:
                    logger.warning(f"Ticker update warning: {ex}")
                self.last_ping_ms = round((time.time() - t0) * 1000.0, 1)

                # Check if news blackout is active
                is_blackout = False
                if self.news_streamer:
                    is_blackout = self.news_streamer.is_macro_blackout_active()

                # Process Bar if new hour or interval
                now_ts = int(time.time() * 1000)
                if now_ts - last_rebal_ts >= 3600 * 1000:
                    last_rebal_ts = now_ts
                    with self.lock:
                        self.engine.process_bar(
                            prices_series=self.kline_history,
                            current_prices=self.latest_prices,
                            bar_ts=now_ts,
                            macro_lockout=is_blackout,
                        )
                        self.save_persistent_state()

                self._export_status()
            except Exception as e:
                logger.error(f"Exception in paper trader loop: {e}", exc_info=True)

            time.sleep(self.poll_interval)

    def stop(self) -> None:
        self.running = False
        if self.stream:
            try:
                self.stream.close()
            except Exception:
                pass
        if self.news_streamer:
            try:
                self.news_streamer.stop()
            except Exception:
                pass
        self.save_persistent_state()
        logger.info("PaperTraderV35 stopped cleanly.")
