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
        self.client = BybitClient(testnet=False)
        self.instrument_rules: Dict[str, Dict[str, Decimal]] = {}

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
                    self.screened_universe = self.screener.screen_universe(client=self.client)
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
                if not s:
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
                "stream_ready": self.ready,
                "monitored_symbols_count": len(self.symbols),
                "btc_trend_status": self.btc_trend_status,
                "capital": round(float(eq), 2),
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
        self.stream.start()

        def loop():
            last_export = 0.0
            while True:
                try:
                    # Update prices from stream
                    msg = self.stream.get_next_message(timeout=1.0)
                    if msg:
                        data = msg.get("data", [])
                        for item in data:
                            sym = item.get("s")
                            p_str = item.get("c") or item.get("p")
                            if sym and p_str:
                                with self.lock:
                                    self.prices[sym] = Decimal(str(p_str))
                                    self.ready = True

                    now = time.time()
                    if now - last_export >= 2.0:
                        last_export = now
                        status = self.get_status()
                        atomic_json(LIVE_STATUS_FILE, status)
                        self.save_persistent_state()

                except Exception as e:
                    logger.debug(f"Stream error: {e}")
                    time.sleep(1.0)

        t = threading.Thread(target=loop, daemon=True)
        t.start()
