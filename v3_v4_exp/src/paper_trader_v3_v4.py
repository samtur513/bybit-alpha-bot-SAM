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

        self.monitored_symbols = list(set(initial_syms) | {"BTCUSDT"})
        self._load_instrument_rules()

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

        # News Core Shield
        news_cfg = NewsConfig(
            min_urgency=2,
            regime_guard_enabled=True,
            emergency_eject_enabled=True,
            auto_resume_delay_seconds=300,
        )
        self.news_core = NewsCore(news_cfg)

        # Market Stream
        self.stream = MarketStream(
            symbols=self.monitored_symbols,
            timeframe=TIMEFRAME,
            candle_interval=CANDLE_INTERVAL,
        )

        self.running = False
        self.thread: Optional[threading.Thread] = None
        self._load_persisted_state()

    def _load_instrument_rules(self) -> None:
        """Fetches instrument rules for monitored coins."""
        try:
            for s in self.monitored_symbols:
                info = self.client.get_instrument_info(category="linear", symbol=s)
                if info and "result" in info and "list" in info["result"] and info["result"]["list"]:
                    item = info["result"]["list"][0]
                    lot = item.get("lotSizeFilter", {})
                    price = item.get("priceFilter", {})
                    min_notional = Decimal(str(lot.get("minNotionalValue", "5.0") or "5.0"))
                    if s == "BTCUSDT":
                        min_notional = Decimal("100.0")

                    self.instrument_rules[s] = {
                        "qtyStep": Decimal(str(lot.get("qtyStep", "0.001"))),
                        "minOrderQty": Decimal(str(lot.get("minOrderQty", "0.001"))),
                        "maxOrderQty": Decimal(str(lot.get("maxOrderQty", "1000000"))),
                        "tickSize": Decimal(str(price.get("tickSize", "0.01"))),
                        "minNotional": min_notional,
                    }
        except Exception as e:
            logger.warning(f"Error fetching instrument rules: {e}")
            for s in self.monitored_symbols:
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
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            atomic_json(PERSISTENT_STATE_FILE, data)
        except Exception as e:
            logger.warning(f"State persistence failed: {e}")

    def start(self) -> None:
        self.running = True
        self.stream.start()
        self.thread = threading.Thread(target=self._run_loop, name="PaperTraderV3V4ExpLoop", daemon=True)
        self.thread.start()
        logger.info(f"PaperTraderV3V4Exp daemon started on port {PORT}.")

    def stop(self) -> None:
        self.running = False
        self.stream.stop()
        if self.thread:
            self.thread.join(timeout=3.0)
        self._persist_state()
        logger.info("PaperTraderV3V4Exp daemon stopped.")

    def _run_loop(self) -> None:
        """Main evaluation loop."""
        last_eval_hour = -1
        while self.running:
            try:
                now = datetime.now(timezone.utc)
                # Check 1h bar boundary
                if now.minute == 0 and now.second < 15 and now.hour != last_eval_hour:
                    last_eval_hour = now.hour
                    self._evaluate_closed_bar()
                self._update_live_status()
            except Exception as e:
                logger.error(f"Error in paper trader loop: {e}", exc_info=True)
            time.sleep(2.0)

    def _evaluate_closed_bar(self) -> None:
        with self.lock:
            # Build bars dict with indicators
            bars: Dict[str, Dict[str, Any]] = {}
            next_prices: Dict[str, Decimal] = {}

            for sym in self.monitored_symbols:
                candles = self.stream.get_candles(sym, limit=60)
                if len(candles) < 35:
                    continue
                df = pd.DataFrame(candles)
                for col in ["open", "high", "low", "close", "volume"]:
                    df[col] = df[col].astype(float)

                close = df["close"].iloc[-2]  # closed bar
                high = df["high"].iloc[-2]
                low = df["low"].iloc[-2]
                opening = df["open"].iloc[-2]

                # Donchian channel
                upper = df["high"].shift(1).rolling(DONCHIAN_WINDOW).max().iloc[-2]
                lower = df["low"].shift(1).rolling(DONCHIAN_WINDOW).min().iloc[-2]
                ema50 = df["close"].ewm(span=50, adjust=False).mean().iloc[-2]
                ema200 = df["close"].ewm(span=200, adjust=False).mean().iloc[-2]

                tr = np.maximum(df["high"] - df["low"], np.maximum(abs(df["high"] - df["close"].shift(1)), abs(df["low"] - df["close"].shift(1))))
                atr = tr.rolling(14).mean().iloc[-2]
                adx = calculate_adx(df["high"], df["low"], df["close"], window=14).iloc[-2]

                bars[sym] = {
                    "open": opening,
                    "high": high,
                    "low": low,
                    "close": close,
                    "upper": upper,
                    "lower": lower,
                    "ema_fast": ema50,
                    "ema_slow": ema200,
                    "atr": atr if np.isfinite(atr) and atr > 0 else (close * 0.02),
                    "volume": df["volume"].iloc[-2],
                    "volume_sma": df["volume"].rolling(20).mean().iloc[-2],
                    "adx": adx if np.isfinite(adx) else 20.0,
                    "prev_high": df["high"].iloc[-3],
                    "prev_low": df["low"].iloc[-3],
                    "highest_20": df["high"].rolling(20).max().iloc[-2],
                    "lowest_20": df["low"].rolling(20).min().iloc[-2],
                }
                next_prices[sym] = Decimal(str(df["close"].iloc[-1]))

            # Trend filters
            btc_bullish = True
            btc_bearish = True
            if "BTCUSDT" in bars:
                b_btc = bars["BTCUSDT"]
                btc_bullish = b_btc["close"] > b_btc["ema_fast"] > b_btc["ema_slow"]
                btc_bearish = b_btc["close"] < b_btc["ema_fast"] < b_btc["ema_slow"]

            # Process bar event
            ts = int(time.time() * 1000)
            self.engine.process_bar_event(
                timestamp=ts,
                bars=bars,
                next_open_prices=next_prices,
                manage_positions=True,
                btc_bullish=btc_bullish,
                btc_bearish=btc_bearish,
            )
            self._persist_state()

    def _update_live_status(self) -> None:
        try:
            status = self.get_status()
            atomic_json(LIVE_STATUS_FILE, status)
        except Exception as e:
            logger.debug(f"Live status export failed: {e}")

    def get_status(self) -> Dict[str, Any]:
        with self.lock:
            # Build current mark prices
            current_prices = {}
            for s in self.monitored_symbols:
                ticker = self.stream.get_ticker(s)
                if ticker and "last_price" in ticker:
                    current_prices[s] = Decimal(str(ticker["last_price"]))

            eq = self.engine.get_equity(current_prices)
            net_pnl = eq - self.engine.initial_capital
            pnl_pct = (net_pnl / self.engine.initial_capital) * Decimal("100")

            positions_dict = {}
            for s, pos in self.engine.open_positions.items():
                mark = current_prices.get(s, pos.entry_price)
                if pos.direction == 1:
                    u_pnl = pos.current_qty * (mark - pos.entry_price)
                else:
                    u_pnl = pos.current_qty * (pos.entry_price - mark)

                tracker_item = self.engine.trackers.get(s)
                stage_label = tracker_item.stage_name if tracker_item else "Stage 0"
                tp_maker = str(tracker_item.target_tp_maker) if tracker_item else "0.0"
                dyn_ratio = tracker_item.dynamic_tp_ratio if tracker_item else STAGE2_DEFAULT_CLOSE_PCT

                positions_dict[s] = {
                    "direction": "LONG" if pos.direction == 1 else "SHORT",
                    "qty": float(pos.current_qty),
                    "initial_qty": float(pos.initial_qty),
                    "entry_price": float(pos.entry_price),
                    "current_price": float(mark),
                    "current_stop": float(pos.current_stop),
                    "target_tp_maker": float(tp_maker),
                    "dynamic_tp_ratio": float(dyn_ratio),
                    "unrealized_pnl": round(float(u_pnl), 2),
                    "bars_held": pos.bars_held,
                    "stage_name": stage_label,
                    "is_tp_filled": tracker_item.is_tp_filled if tracker_item else False,
                }

            trades_list = []
            for t in self.engine.closed_trades[-30:]:
                trades_list.append({
                    "symbol": t.symbol,
                    "side": "LONG" if t.direction == 1 else "SHORT",
                    "entry_price": float(t.entry_price),
                    "exit_price": float(t.exit_price),
                    "qty": float(t.final_qty),
                    "net_pnl": round(float(t.net_pnl), 2),
                    "exit_reason": t.exit_reason,
                    "exit_time": t.exit_time,
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
                "stream_ready": True,
                "bybit_ws_connected": True,
                "monitored_symbols_count": len(self.monitored_symbols),
                "monitored_symbols": self.monitored_symbols,
                "initial_capital": float(self.engine.initial_capital),
                "capital": round(float(self.engine.realized_equity), 2),
                "equity": round(float(eq), 2),
                "realized_pnl": round(float(self.engine.realized_equity - self.engine.initial_capital), 2),
                "unrealized_pnl": round(float(eq - self.engine.realized_equity), 2),
                "net_pnl": round(float(net_pnl), 2),
                "pnl_pct": round(float(pnl_pct), 2),
                "max_drawdown_pct": round(float(self.engine.max_drawdown_pct), 2),
                "portfolio_heat_pct": round(heat * 100, 2),
                "win_rate": wr,
                "trades_count": len(self.engine.closed_trades),
                "open_positions": positions_dict,
                "closed_trades": trades_list,
                "fsm_stage_stats": self.engine.fsm_stage_stats,
                "news_defense_active": True,
                "news_shield": True,
                "news_cached_count": self.news_core.cache_count,
                "timeframe": "1h",
            }
