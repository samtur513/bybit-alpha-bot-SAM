"""
Paper Trading Daemon v3-v4-exp: NewDragon Trend Lab (Port 8082).
Features:
- Dynamic Universe Screener (Top 16 coins from 400+ Bybit Linear Perpetuals).
- 1h Donchian Breakout with ADX and BTC Delta Trend Filters.
- NewDragon v4.0 Universal True 1R Position Sizer with Fractional Kelly, minNotional Protection,
  Portfolio Heat Guard, and Correlation Protection.
- 4-Stage Adaptive Exit FSM (Stage 0 Fast Scratch, Stage 1 MFE Breakeven Protection,
  Stage 2 Dynamic Maker Partial TP 40-70%, Stage 3 Chandelier 2.5x ATR).
- News Core v1.2 Defensive Shield.
- Atomic State Persistence and Live Telemetry for Meta-Dashboard (:8085).
"""

from collections import deque
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
from pathlib import Path
import queue
import sys
import threading
import time
from typing import Dict, List, Optional, Any, Set
import pandas as pd
import numpy as np

BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent
for p in [str(REPO_ROOT), str(BASE_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from v3.src.bybit_client import BybitClient
from v3.src.market_stream import MarketStream
from v3.src.universe_screener import UniverseScreener, ScreenerConfig, ScreenedAsset
from v3.src.news_core import NewsCore, NewsConfig
from v3.src.persistence import atomic_json
from v3.src.indicators import calculate_adx
from v3_v4_exp.src.config import (
    PORT,
    VERSION_NAME,
    VERSION,
    INITIAL_CAPITAL,
    TIMEFRAME,
    CANDLE_INTERVAL,
    BAR_INTERVAL_MS,
    DONCHIAN_WINDOW,
    MIN_TURNOVER_24H_USDT,
    MAX_ACTIVE_SYMBOLS,
    SCREENER_ROTATION_INTERVAL_HOURS,
    TAKER_FEE,
    MAKER_FEE,
    SLIPPAGE,
    MAX_LEVERAGE,
    MAX_CONCURRENT_POSITIONS,
    STAGE2_R,
    STAGE2_STRONG_TREND_CLOSE_PCT,
    STAGE2_CHOP_CLOSE_PCT,
    STAGE2_DEFAULT_CLOSE_PCT,
    REPORTS_DIR,
    PERSISTENT_STATE_FILE,
    LIVE_STATUS_FILE,
    LIVE_MD_FILE,
)
from v3_v4_exp.src.core_v3_v4 import CoreV3V4ExpEngine

logger = logging.getLogger("PaperTraderV3V4Exp")


class PaperTraderV3V4Exp:
    """Daemon running v3-v4-exp NewDragon Trend Lab paper trading."""

    def __init__(self, symbols: Optional[List[str]] = None):
        self.lock = threading.RLock()
        self.client = BybitClient(timeout=10, max_retries=3)
        self.instrument_rules: Dict[str, Dict[str, Decimal]] = {}
        self.bar_interval_ms = BAR_INTERVAL_MS

        # Screener setup
        self.screener_config = ScreenerConfig(
            min_turnover_24h=MIN_TURNOVER_24H_USDT,
            top_n=MAX_ACTIVE_SYMBOLS,
            min_adx=18.0,
        )
        self.screener = UniverseScreener(self.screener_config)
        self.last_screener_run_ts: float = 0.0
        self.screened_universe: List[ScreenedAsset] = []

        initial_syms = list(symbols) if symbols else []
        if not initial_syms and self.client is not None:
            try:
                top_syms = self.screener.get_top_universe(client=self.client)
                if top_syms:
                    initial_syms = top_syms
                    self.last_screener_run_ts = time.time()
                    logger.info(f"Initialized dynamic universe with {len(initial_syms)} coins from Bybit screener.")
            except Exception as e:
                logger.warning(f"Initial universe screening fallback: {e}")

        if not initial_syms:
            initial_syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "SUIUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT"]

        if "BTCUSDT" not in initial_syms:
            initial_syms.insert(0, "BTCUSDT")

        self.symbols = list(dict.fromkeys(initial_syms))
        self.monitored_symbols = self.symbols
        self._load_instrument_specs()

        # Engine initialization
        self.engine = CoreV3V4ExpEngine(
            initial_capital=INITIAL_CAPITAL,
            instrument_rules=self.instrument_rules,
            taker_fee=TAKER_FEE,
            maker_fee=MAKER_FEE,
            slippage=SLIPPAGE,
            max_leverage=Decimal(str(MAX_LEVERAGE)),
            max_concurrent_positions=MAX_CONCURRENT_POSITIONS,
            bar_interval_ms=self.bar_interval_ms,
        )

        # Buffers and streaming state
        self.candle_buffers: Dict[str, pd.DataFrame] = {}
        self.last_processed_bar_ts: Dict[str, int] = {s: 0 for s in self.symbols}
        self.prices: Dict[str, Decimal] = {}
        self.price_times: Dict[str, float] = {}
        self.batches: Dict[int, Dict[str, Any]] = {}
        self.ready = False
        self.btc_trend_status = "NEUTRAL"

        # News Core Defensive Shield v1.2
        cfg = NewsConfig.defensive_only()
        self.news_core = NewsCore(cfg)

        self.running = False
        self.thread: Optional[threading.Thread] = None
        self._load_persisted_state()
        self.stream = MarketStream(self.symbols, interval=CANDLE_INTERVAL)

    def _load_instrument_specs(self) -> None:
        """Fetches instrument rules for linear perpetual contracts."""
        try:
            info = self.client.get_instruments_info(category="linear")
            for item in info:
                s = item.get("symbol")
                if not s or s not in self.symbols:
                    continue
                lot = item.get("lotSizeFilter", {})
                price = item.get("priceFilter", {})
                min_n = Decimal(str(lot.get("minNotionalValue", "5.0") or "5.0"))
                if s == "BTCUSDT":
                    min_n = Decimal("100.0")

                self.instrument_rules[s] = {
                    "qtyStep": Decimal(str(lot.get("qtyStep", "0.001"))),
                    "minOrderQty": Decimal(str(lot.get("minOrderQty", "0.001"))),
                    "maxOrderQty": Decimal(str(lot.get("maxOrderQty", "1000000"))),
                    "tickSize": Decimal(str(price.get("tickSize", "0.01"))),
                    "minNotional": min_n,
                }
            logger.info(f"Loaded contract specifications for {len(self.instrument_rules)} symbols.")
        except Exception as e:
            logger.warning(f"Error fetching instrument rules: {e}")

        for s in self.symbols:
            if s not in self.instrument_rules:
                min_n = Decimal("100.0") if s == "BTCUSDT" else Decimal("5.0")
                self.instrument_rules[s] = {
                    "qtyStep": Decimal("0.001"),
                    "minOrderQty": Decimal("0.001"),
                    "maxOrderQty": Decimal("1000000"),
                    "tickSize": Decimal("0.01"),
                    "minNotional": min_n,
                }

    def _load_persisted_state(self) -> None:
        if not PERSISTENT_STATE_FILE.exists():
            return
        try:
            with open(PERSISTENT_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.engine.realized_equity = Decimal(str(data.get("realized_equity", INITIAL_CAPITAL)))
            self.engine.peak_equity = Decimal(str(data.get("peak_equity", self.engine.realized_equity)))
            self.engine.max_drawdown_pct = Decimal(str(data.get("max_drawdown_pct", "0")))
            logger.info(f"Loaded persisted state. Equity: {self.engine.realized_equity} USDT")
        except Exception as e:
            logger.warning(f"Failed to load state: {e}")

    def _persist_state(self) -> None:
        try:
            data = {
                "version": VERSION,
                "realized_equity": str(self.engine.realized_equity),
                "peak_equity": str(self.engine.peak_equity),
                "max_drawdown_pct": str(self.engine.max_drawdown_pct),
                "closed_trades_count": len(self.engine.closed_trades),
                "open_positions_count": len(self.engine.open_positions),
                "fsm_stage_stats": self.engine.fsm_stage_stats,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            atomic_json(PERSISTENT_STATE_FILE, data)
        except Exception as e:
            logger.warning(f"State persistence failed: {e}")

    def get_live_prices(self) -> Dict[str, Decimal]:
        """Returns snapshot of current mark prices."""
        with self.lock:
            return dict(self.prices)

    def _compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Computes Donchian Channel, ATR, EMAs, and ADX for 1h candles with Zero-Lookahead PIT."""
        res = df.copy()
        high = res["high"]
        low = res["low"]
        close = res["close"]

        # Zero-lookahead Donchian channel using shift(1)
        res["upper"] = high.rolling(DONCHIAN_WINDOW).max().shift(1)
        res["lower"] = low.rolling(DONCHIAN_WINDOW).min().shift(1)

        # 20-bar extremes for trailing / Chandelier stops
        res["highest_20"] = high.rolling(DONCHIAN_WINDOW).max().shift(1)
        res["lowest_20"] = low.rolling(DONCHIAN_WINDOW).min().shift(1)
        res["prev_high"] = high.shift(1)
        res["prev_low"] = low.shift(1)

        # EMAs
        res["ema_fast"] = close.ewm(span=50, adjust=False).mean()
        res["ema_slow"] = close.ewm(span=200, adjust=False).mean()

        # ATR
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        res["atr"] = tr.rolling(14).mean()

        # Volume SMA
        res["volume_sma"] = res["volume"].rolling(20).mean()

        # ADX
        try:
            res["adx"] = calculate_adx(high, low, close, period=14)
        except Exception:
            res["adx"] = 25.0

        return res

    def _fetch_history(self) -> Dict[str, pd.DataFrame]:
        """Fetches historical 1h klines for technical indicators."""
        buffers = {}
        now = int(time.time() * 1000)
        try:
            now = self.client.get_server_time()
        except Exception:
            pass

        for sym in self.symbols:
            try:
                raw = self.client.get_kline(sym, interval=CANDLE_INTERVAL, limit=100)
                records = [
                    dict(
                        timestamp=int(k[0]),
                        **{name: float(k[i]) for i, name in enumerate(("open", "high", "low", "close", "volume", "turnover"), 1)},
                    )
                    for k in raw
                    if int(k[0]) + self.bar_interval_ms <= now
                ]
                if not records:
                    continue
                df = (
                    pd.DataFrame(records)
                    .sort_values("timestamp")
                    .drop_duplicates("timestamp")
                    .reset_index(drop=True)
                )
                if len(df) >= min(DONCHIAN_WINDOW, 20):
                    df = self._compute_indicators(df)
                    buffers[sym] = df
            except Exception as e:
                logger.warning(f"Error fetching history for {sym}: {e}")
        return buffers

    def _on_bar(self, sym: str, bar: Dict[str, Any]) -> None:
        """Handles confirmed 1h bar from Bybit WebSocket."""
        ts = int(bar.get("start", 0))
        previous = self.last_processed_bar_ts.get(sym, 0)
        if ts <= previous:
            return
        record = dict(
            timestamp=ts,
            **{k: float(bar[k]) for k in ("open", "high", "low", "close", "volume", "turnover") if k in bar},
        )
        df = self.candle_buffers.get(sym)
        if df is None:
            return
        df = pd.concat(
            [df[["timestamp", "open", "high", "low", "close", "volume", "turnover"]], pd.DataFrame([record])],
            ignore_index=True,
        ).tail(200)
        df = self._compute_indicators(df)
        self.candle_buffers[sym] = df
        self.last_processed_bar_ts[sym] = ts
        self.batches.setdefault(ts, {})[sym] = df.iloc[-1].to_dict()

    def _process_bar_batch(self, ts: int, bars: Dict[str, Any]) -> None:
        """Processes a synchronized batch of bars across symbols."""
        prices = self.get_live_prices()
        btc_bar = bars.get("BTCUSDT")
        btc_bullish = True
        btc_bearish = True
        if btc_bar:
            c = float(btc_bar["close"])
            ef = float(btc_bar.get("ema_fast", 0))
            es = float(btc_bar.get("ema_slow", 0))
            btc_bullish = ef > es and c > ef
            btc_bearish = ef < es and c < ef
            self.btc_trend_status = "BULLISH" if btc_bullish else ("BEARISH" if btc_bearish else "NEUTRAL")

        blocked_symbols = set()
        nc = getattr(self, "news_core", None)
        if nc:
            bar_dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            for s in self.symbols:
                st = nc.get_status(s, bar_dt)
                if st.get("block_new_entries"):
                    blocked_symbols.add(s)

        open_prices = {s: prices.get(s, Decimal(str(b["close"]))) for s, b in bars.items()}

        self.engine.process_bar_event(
            timestamp=ts,
            bars=bars,
            next_open_prices=open_prices,
            manage_positions=True,
            btc_bullish=btc_bullish,
            btc_bearish=btc_bearish,
            blocked_symbols=blocked_symbols,
        )

    def _signals(self) -> None:
        """Evaluates signals on closed 1h bars."""
        now_ms = int(time.time() * 1000)
        for ts, bars in list(self.batches.items()):
            time_since_close = now_ms - (ts + self.bar_interval_ms)
            if time_since_close > 180000:
                del self.batches[ts]
                continue

            has_all_bars = set(bars) == set(self.symbols)
            grace_period_expired = time_since_close >= 30000
            has_quorum = ("BTCUSDT" in bars) and (len(bars) >= max(3, int(len(self.symbols) * 0.70)))

            if self.ready and (has_all_bars or (grace_period_expired and has_quorum)):
                self._process_bar_batch(ts, bars)
                del self.batches[ts]

    def get_status(self) -> Dict[str, Any]:
        with self.lock:
            current_prices = dict(self.prices)
            eq = self.engine.get_equity(current_prices)
            peak = self.engine.peak_equity
            dd = float(self.engine.max_drawdown_pct)
            net_pnl = eq - self.engine.initial_capital
            pnl_pct = (net_pnl / self.engine.initial_capital) * Decimal("100")

            positions_dict = {}
            for s, pos in self.engine.open_positions.items():
                p_entry = pos.get("entry_price") if isinstance(pos, dict) else getattr(pos, "entry_price", Decimal("0"))
                p_dir = pos.get("direction", 1) if isinstance(pos, dict) else getattr(pos, "direction", 1)
                p_qty = pos.get("current_qty") if isinstance(pos, dict) else getattr(pos, "current_qty", Decimal("0"))
                p_ini_qty = pos.get("initial_qty") if isinstance(pos, dict) else getattr(pos, "initial_qty", p_qty)
                p_stop = pos.get("current_stop") if isinstance(pos, dict) else getattr(pos, "current_stop", Decimal("0"))
                p_bars = pos.get("bars_held", 0) if isinstance(pos, dict) else getattr(pos, "bars_held", 0)

                mark = current_prices.get(s, p_entry)
                if p_dir == 1:
                    u_pnl = p_qty * (mark - p_entry)
                else:
                    u_pnl = p_qty * (p_entry - mark)
                u_pnl -= p_qty * mark * self.taker_fee

                tracker_item = self.engine.trackers.get(s)
                stage_label = tracker_item.stage_name if tracker_item else "Stage 0"
                tp_maker = float(tracker_item.target_tp_maker) if tracker_item else 0.0
                dyn_ratio = tracker_item.dynamic_tp_ratio if tracker_item else STAGE2_DEFAULT_CLOSE_PCT

                positions_dict[s] = {
                    "symbol": s,
                    "direction": "LONG" if p_dir == 1 else "SHORT",
                    "qty": float(p_qty),
                    "initial_qty": float(p_ini_qty),
                    "entry_price": float(p_entry),
                    "current_price": float(mark),
                    "current_stop": float(p_stop),
                    "target_tp_maker": round(tp_maker, 4),
                    "dynamic_tp_ratio": float(dyn_ratio),
                    "unrealized_pnl": round(float(u_pnl), 2),
                    "bars_held": p_bars,
                    "stage_name": stage_label,
                    "is_tp_filled": tracker_item.is_tp_filled if tracker_item else False,
                }

            trades_list = []
            for t in reversed(self.engine.closed_trades[-30:]):
                t_sym = t.get("symbol") if isinstance(t, dict) else getattr(t, "symbol", "")
                t_dir = t.get("direction", 1) if isinstance(t, dict) else getattr(t, "direction", 1)
                t_ep = t.get("entry_price", 0.0) if isinstance(t, dict) else getattr(t, "entry_price", 0.0)
                t_xp = t.get("exit_price", 0.0) if isinstance(t, dict) else getattr(t, "exit_price", 0.0)
                t_qty = t.get("final_qty", t.get("qty", 0.0)) if isinstance(t, dict) else getattr(t, "final_qty", 0.0)
                t_pnl = t.get("net_pnl", 0.0) if isinstance(t, dict) else getattr(t, "net_pnl", 0.0)
                t_reason = t.get("exit_reason", "") if isinstance(t, dict) else getattr(t, "exit_reason", "")
                t_time = t.get("exit_time", "") if isinstance(t, dict) else getattr(t, "exit_time", "")
                t_hold = t.get("hold_hours", 0.0) if isinstance(t, dict) else getattr(t, "hold_hours", 0.0)

                trades_list.append({
                    "symbol": t_sym,
                    "side": "LONG" if t_dir == 1 else "SHORT",
                    "direction": "LONG" if t_dir == 1 else "SHORT",
                    "entry_price": float(t_ep),
                    "exit_price": float(t_xp),
                    "qty": float(t_qty),
                    "net_pnl": round(float(t_pnl), 2),
                    "exit_reason": t_reason,
                    "exit_time": t_time,
                    "hold_hours": round(float(t_hold), 1),
                })

            wr = 0.0
            if self.engine.closed_trades:
                wins = sum(1 for t in self.engine.closed_trades if t.net_pnl > 0)
                wr = round((wins / len(self.engine.closed_trades)) * 100, 1)

            heat = self.engine.get_current_portfolio_heat(current_prices)

            return {
                "version": VERSION,
                "version_name": VERSION_NAME,
                "port": PORT,
                "mode": "PAPER_TRADING",
                "status": "RUNNING" if not self.engine.trading_halted else "CIRCUIT_BREAKER_HALT",
                "stream_ready": self.ready,
                "bybit_ws_connected": True,
                "monitored_symbols_count": len(self.symbols),
                "monitored_symbols": self.symbols,
                "btc_trend_status": self.btc_trend_status,
                "initial_capital": float(self.engine.initial_capital),
                "capital": round(float(self.engine.realized_equity), 2),
                "equity": round(float(eq), 2),
                "peak_equity": round(float(peak), 2),
                "realized_pnl": round(float(self.engine.realized_equity - self.engine.initial_capital), 2),
                "unrealized_pnl": round(float(eq - self.engine.realized_equity), 2),
                "net_pnl": round(float(net_pnl), 2),
                "total_net_pnl": round(float(net_pnl), 2),
                "pnl_pct": round(float(pnl_pct), 2),
                "max_drawdown_pct": round(float(self.engine.max_drawdown_pct), 2),
                "drawdown_pct": round(float(self.engine.max_drawdown_pct), 2),
                "portfolio_heat_pct": round(heat * 100, 2),
                "win_rate": wr,
                "win_rate_pct": wr,
                "trades_count": len(self.engine.closed_trades),
                "closed_trades_count": len(self.engine.closed_trades),
                "open_positions_count": len(positions_dict),
                "open_positions": positions_dict,
                "closed_trades": trades_list,
                "fsm_stage_stats": self.engine.fsm_stage_stats,
                "news_defense_active": True,
                "news_shield": True,
                "news_cached_count": getattr(self.news_core, "cache_count", 0),
                "timeframe": "1h",
                "last_update": datetime.now(timezone.utc).isoformat(),
            }

    def start(self) -> None:
        """Starts background streaming and trading loop."""
        self.running = True
        logger.info(f"Starting {VERSION_NAME} daemon on Port {PORT}...")

        def init_and_loop():
            try:
                self.candle_buffers = self._fetch_history()
                for sym, df in self.candle_buffers.items():
                    if len(df) > 0:
                        self.last_processed_bar_ts[sym] = int(df.timestamp.iloc[-1])
                        c = Decimal(str(df.close.iloc[-1]))
                        self.prices[sym] = c
                self.ready = True
                btc_df = self.candle_buffers.get("BTCUSDT")
                if btc_df is not None and len(btc_df) > 0:
                    last = btc_df.iloc[-1]
                    c = float(last["close"])
                    ef = float(last.get("ema_fast", 0))
                    es = float(last.get("ema_slow", 0))
                    if ef > es and c > ef:
                        self.btc_trend_status = "BULLISH"
                    elif ef < es and c < ef:
                        self.btc_trend_status = "BEARISH"
                    else:
                        self.btc_trend_status = "NEUTRAL"
                logger.info(f"Loaded historical candles for {len(self.candle_buffers)} symbols. BTC: {self.btc_trend_status}")
                # Evaluate the latest closed bar immediately on startup
                if self.candle_buffers:
                    latest_timestamps = [int(df.timestamp.iloc[-1]) for df in self.candle_buffers.values() if len(df) > 0]
                    if latest_timestamps:
                        target_ts = max(set(latest_timestamps), key=latest_timestamps.count)
                        initial_bars = {
                            s: df.iloc[-1].to_dict()
                            for s, df in self.candle_buffers.items()
                            if len(df) > 0 and int(df.timestamp.iloc[-1]) == target_ts
                        }
                        if len(initial_bars) >= max(3, int(len(self.symbols) * 0.70)):
                            with self.lock:
                                self._process_bar_batch(target_ts, initial_bars)
            except Exception as e:
                logger.warning(f"History load warning: {e}")
                self.ready = True

            self.stream.start()

            last_export = 0.0
            while self.running:
                try:
                    try:
                        kind, sym, ts, data, received = self.stream.events.get(timeout=0.2)
                    except queue.Empty:
                        kind = None

                    if kind == "reset":
                        self.ready = False
                        new_buffers = self._fetch_history()
                        with self.lock:
                            self.candle_buffers = new_buffers
                            self.ready = True
                    elif kind:
                        with self.lock:
                            if kind == "price":
                                price = Decimal(str(data))
                                if price.is_finite() and price > 0:
                                    self.prices[sym] = price
                                    self.price_times[sym] = received
                                    self.engine._observe(self.prices)
                            elif kind == "bar":
                                self._on_bar(sym, data)

                    with self.lock:
                        self._signals()

                    now = time.time()
                    if now - last_export >= 2.0:
                        last_export = now
                        status = self.get_status()
                        atomic_json(LIVE_STATUS_FILE, status)
                        self._persist_state()

                except Exception as e:
                    logger.debug(f"Stream error: {e}")
                    time.sleep(0.5)

        self.thread = threading.Thread(target=init_and_loop, name="PaperTraderV3V4ExpLoop", daemon=True)
        self.thread.start()
        logger.info(f"PaperTraderV3V4Exp daemon running on port {PORT}.")

    def stop(self) -> None:
        self.running = False
        self.stream.stop()
        if self.thread:
            self.thread.join(timeout=3.0)
        self._persist_state()
        logger.info("PaperTraderV3V4Exp daemon stopped.")
