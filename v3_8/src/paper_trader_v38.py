"""
Paper Trading Daemon v3.8 NewDragon Trend Lab (Port 8091).
Features:
- Dynamic Universe Screener (Top 16 coins from 400+ Bybit Linear Perpetuals).
- 1h Donchian Breakout with ADX and BTC Delta Trend Filters.
- NewDragon v4.0 Universal True 1R Position Sizer with Fractional Kelly and minNotional Protection.
- 4-Stage Adaptive Exit FSM (Stage 0 Fast Scratch, Stage 1 Ratchet, Stage 2 Maker Partial TP 60%, Stage 3 Chandelier).
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
from v3_8.src.config import (
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
    STAGE2_CLOSE_PCT,
    STAGE2_R,
    REPORTS_DIR,
    PERSISTENT_STATE_FILE,
    LIVE_STATUS_FILE,
    LIVE_MD_FILE,
)
from v3_8.src.core_v38 import CoreV38Engine

logger = logging.getLogger("PaperTraderV38")


class PaperTraderV38:
    """Daemon running v3.8 NewDragon Trend Lab paper trading."""

    def __init__(self, symbols: Optional[List[str]] = None):
        self.lock = threading.Lock()
        self.api_key = ""
        self.api_secret = ""
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
                logger.warning(f"Initial universe screening fallback to default: {e}")

        if not initial_syms:
            initial_syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "NEARUSDT", "SUIUSDT", "ADAUSDT", "LINKUSDT", "AVAXUSDT"]

        if "BTCUSDT" not in initial_syms:
            initial_syms.insert(0, "BTCUSDT")

        self.symbols = initial_syms
        self._load_instrument_specs()

        self.engine = CoreV38Engine(
            initial_capital=INITIAL_CAPITAL,
            instrument_rules=self.instrument_rules,
            taker_fee=TAKER_FEE,
            maker_fee=MAKER_FEE,
            slippage=SLIPPAGE,
            max_leverage=Decimal(str(MAX_LEVERAGE)),
            max_concurrent_positions=MAX_CONCURRENT_POSITIONS,
            bar_interval_ms=BAR_INTERVAL_MS,
        )

        self.candle_buffers: Dict[str, pd.DataFrame] = {}
        self.last_processed_bar_ts: Dict[str, int] = {s: 0 for s in self.symbols}
        self.prices: Dict[str, Decimal] = {}
        self.price_times: Dict[str, float] = {}
        self.batches: Dict[int, Dict[str, Any]] = {}
        self.daily_date = ""
        self.ready = False
        self.btc_trend_status = "NEUTRAL"
        self.last_status_cache: Dict[str, Any] = {}

        self._load_persistent_state()
        self.stream = MarketStream(self.symbols, interval=CANDLE_INTERVAL)

        # News Core Defensive Shield v1.2
        cfg = NewsConfig.defensive_only()
        self.news_core = NewsCore(cfg)

    def _load_instrument_specs(self) -> None:
        """Loads lot size and minNotional filters for linear perpetual contracts."""
        try:
            info = self.client.get_instruments_info(category="linear")
            for item in info:
                s = item.get("symbol")
                if not s or s not in self.symbols:
                    continue
                lot = item.get("lotSizeFilter", {})
                self.instrument_rules[s] = {
                    "qtyStep": Decimal(str(lot.get("qtyStep", "0.001"))),
                    "minOrderQty": Decimal(str(lot.get("minOrderQty", "0.001"))),
                    "maxOrderQty": Decimal(str(lot.get("maxOrderQty", "1000000"))),
                    "minNotional": Decimal(str(lot.get("minNotionalValue", "5.0"))),
                }
            logger.info(f"Loaded contract specifications for {len(self.instrument_rules)} symbols.")
        except Exception as e:
            logger.error(f"Failed to load instrument specifications: {e}")

        for s in self.symbols:
            if s not in self.instrument_rules:
                self.instrument_rules[s] = {
                    "qtyStep": Decimal("0.001"),
                    "minOrderQty": Decimal("0.001"),
                    "maxOrderQty": Decimal("1000000"),
                    "minNotional": Decimal("5.0"),
                }

    def _load_persistent_state(self) -> None:
        """Loads state from STATE_V38.json if present."""
        if not PERSISTENT_STATE_FILE.exists():
            return
        try:
            with open(PERSISTENT_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.engine.realized_equity = Decimal(str(data.get("realized_equity", INITIAL_CAPITAL)))
            self.engine.peak_equity = Decimal(str(data.get("peak_equity", INITIAL_CAPITAL)))
            self.engine.max_drawdown_pct = Decimal(str(data.get("max_drawdown_pct", "0")))
            logger.info(f"Loaded persistent state: Realized Equity=${self.engine.realized_equity:.2f}")
        except Exception as e:
            logger.warning(f"Error loading persistent state: {e}")

    def save_persistent_state(self) -> None:
        """Persists engine state atomically."""
        data = {
            "version": VERSION,
            "realized_equity": str(self.engine.realized_equity),
            "peak_equity": str(self.engine.peak_equity),
            "max_drawdown_pct": str(self.engine.max_drawdown_pct),
            "open_positions_count": len(self.engine.open_positions),
            "closed_trades_count": len(self.engine.closed_trades),
            "fsm_stage_stats": self.engine.fsm_stage_stats,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        atomic_json(PERSISTENT_STATE_FILE, data)

    def get_live_prices(self) -> Dict[str, Decimal]:
        """Returns snapshot of current mark prices."""
        with self.lock:
            return dict(self.prices)

    def _compute_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Computes Donchian Channel, ATR, EMAs, and ADX."""
        res = df.copy()
        high = res["high"]
        low = res["low"]
        close = res["close"]

        # Donchian channel using shift(1) for Zero-Lookahead PIT
        res["upper"] = high.rolling(DONCHIAN_WINDOW).max().shift(1)
        res["lower"] = low.rolling(DONCHIAN_WINDOW).min().shift(1)

        # 20-bar extremes for Chandelier trailing stop
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

    def _signals(self) -> None:
        """Evaluates signals on closed 1h bars."""
        prices = self.get_live_prices()
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
                del self.batches[ts]

    def get_status(self) -> Dict[str, Any]:
        """Provides status dict for Web Dashboard and Meta-Dashboard."""
        with self.lock:
            current_prices = dict(self.prices)
            eq = self.engine.get_equity(current_prices)
            peak = self.engine.peak_equity
            dd = float(self.engine.max_drawdown_pct)
            net_pnl = float(self.engine.realized_equity - INITIAL_CAPITAL)

            open_pos = {}
            for sym, pos in self.engine.open_positions.items():
                mark = float(current_prices.get(sym, pos.entry_price))
                entry = float(pos.entry_price)
                qty = float(pos.current_qty)
                unrealized = qty * (mark - entry) if pos.direction == 1 else qty * (entry - mark)
                unrealized -= qty * mark * float(TAKER_FEE)

                tracker_item = self.engine.trackers.get(sym)
                stage_name = tracker_item.stage_name if tracker_item else "Stage 0: Initial"
                tp_maker = float(tracker_item.target_tp_maker) if tracker_item else 0.0

                open_pos[sym] = {
                    "symbol": sym,
                    "direction": "LONG" if pos.direction == 1 else "SHORT",
                    "qty": qty,
                    "entry_price": entry,
                    "current_price": mark,
                    "current_stop": float(pos.current_stop),
                    "unrealized_pnl": round(unrealized, 4),
                    "stage_name": stage_name,
                    "tp_maker_price": round(tp_maker, 4),
                    "bars_held": pos.bars_held,
                }

            closed = []
            for t in reversed(self.engine.closed_trades[-30:]):
                closed.append({
                    "symbol": t.symbol,
                    "direction": "LONG" if t.direction == 1 else "SHORT",
                    "entry_price": float(t.entry_price),
                    "exit_price": float(t.exit_price),
                    "qty": float(t.final_qty),
                    "net_pnl": float(t.net_pnl),
                    "exit_reason": t.exit_reason,
                    "hold_hours": round(t.hold_hours, 1),
                })

            win_count = sum(1 for t in self.engine.closed_trades if t.net_pnl > 0)
            tot_count = len(self.engine.closed_trades)
            win_rate = (win_count / tot_count * 100.0) if tot_count > 0 else 0.0

            return {
                "version": VERSION,
                "version_name": VERSION_NAME,
                "port": PORT,
                "timeframe": TIMEFRAME,
                "mode": "PAPER_STREAM_V38_NEWDRAGON",
                "status": "RUNNING",
                "stream_ready": True,
                "bybit_ws_connected": True,
                "monitored_symbols_count": len(self.symbols),
                "btc_trend_status": self.btc_trend_status,
                "news_defense_active": True,
                "news_shield": True,
                "news_profile": "v1.2 Shield",
                "capital": round(float(eq), 2),
                "equity": round(float(eq), 2),
                "peak_equity": round(float(peak), 2),
                "drawdown_pct": round(dd, 2),
                "total_net_pnl": round(net_pnl, 2),
                "win_rate_pct": round(win_rate, 1),
                "closed_trades_count": tot_count,
                "open_positions_count": len(open_pos),
                "open_positions": open_pos,
                "closed_trades": closed,
                "fsm_stage_stats": self.engine.fsm_stage_stats,
                "last_update": datetime.now(timezone.utc).isoformat(),
            }

    def start(self) -> None:
        """Starts background streaming and trading loop."""
        logger.info(f"Starting {VERSION_NAME} daemon on Port {PORT}...")

        # Initial history load in background thread to avoid blocking dashboard startup
        def init_history_and_stream():
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
            except Exception as e:
                logger.warning(f"History load warning: {e}")
                self.ready = True

            self.stream.start()

            last_export = 0.0
            while True:
                try:
                    try:
                        kind, sym, ts, data, received = self.stream.events.get(timeout=0.2)
                    except queue.Empty:
                        kind = None

                    with self.lock:
                        if kind == "price":
                            price = Decimal(str(data))
                            if price.is_finite() and price > 0:
                                self.prices[sym] = price
                                self.price_times[sym] = received
                                self.engine._observe(self.prices)
                        elif kind == "bar":
                            self._on_bar(sym, data)
                        elif kind == "reset":
                            self.ready = False
                            self.candle_buffers = self._fetch_history()
                            self.ready = True

                        self._signals()

                        now = time.time()
                        if now - last_export >= 2.0:
                            last_export = now
                            status = self.get_status()
                            atomic_json(LIVE_STATUS_FILE, status)
                            self.save_persistent_state()

                except Exception as e:
                    logger.debug(f"Stream error: {e}")
                    time.sleep(0.5)

        t = threading.Thread(target=init_history_and_stream, daemon=True)
        t.start()
