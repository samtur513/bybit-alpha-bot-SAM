"""
Paper Trading Daemon v3.2 Dual-Regime (Port 8083).
Combines:
1. Dynamic Universe Screener across 400+ Bybit Linear Perpetuals (rotated every 2 hours)
2. Trend Engine (1h Donchian Breakout, Auto-Gearbox Gear 3, Half-Kelly sizing, Pyramiding, Cascade TP)
3. Range Mean-Reversion Engine (Limit Maker orders in Chop/Range, TP at SMA20, SL at 1.0 ATR, Maker fee 0.0324%)
4. Regime Handoff (seamless exit from Range to Trend upon momentum surge)
5. Strict Penny-Perfect Trade Accounting Invariant across shared capital ($300.00 base)
6. Zero-Lookahead Point-in-Time execution protocol
"""

import collections
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
import json
import logging
import os
from pathlib import Path
import queue
import threading
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

from v3.src.bybit_client import BybitClient
from v3.src.market_stream import MarketStream
from v3.src.persistence import atomic_json
from v3.src.telegram_notifier import TelegramNotifier
from v3.src.universe_screener import UniverseScreener, ScreenerConfig, ScreenedAsset
from v3.src.core_v3 import CoreV3Engine, ActivePosition, ClosedTrade, PortfolioSnapshot
from v3.src.news_core.news_config import NewsConfig
from v3.src.news_core.regime_guard import RegimeGuard

from .chop_screener import ChopUniverseScreener, ChopScreenerConfig, ChopCandidateMetric
from .config import (
    PORT,
    DEFAULT_SYMBOLS,
    INITIAL_CAPITAL,
    BASE_RISK_PCT,
    MAX_LEVERAGE_CAP,
    PORTFOLIO_LEVERAGE,
    DRAWDOWN_LIMIT_PCT,
    CANDLE_INTERVAL,
    BAR_INTERVAL_MS,
    DONCHIAN_WINDOW,
    MIN_ADX_TREND,
    ENABLE_DYNAMIC_UNIVERSE,
    UNIVERSE_TOP_N,
    TREND_UNIVERSE_TOP_N,
    SCREENER_INTERVAL_HOURS,
    MIN_24H_TURNOVER,
    MIN_OPEN_INTEREST,
    MAX_FUNDING_RATE_ABS,
    MIN_NORM_ATR,
    ENABLE_RVOL_FILTER,
    MIN_RVOL,
    ENABLE_DELTA_OI_FILTER,
    ENABLE_ATR_EXHAUSTION_GUARD,
    MAX_DAILY_ATR_EXHAUSTION,
    ENABLE_CHOP_SCREENER,
    CHOP_UNIVERSE_TOP_N,
    CHOP_MIN_24H_TURNOVER,
    CHOP_MIN_OPEN_INTEREST,
    CHOP_MAX_KER,
    CHOP_MIN_BANDWIDTH_PCT,
    CHOP_MAX_ADX,
    CHOP_RESCAN_INTERVAL_HOURS,
    MIN_BANDWIDTH_CLEARANCE,
    RANGE_RISK_PER_TRADE_PCT,
    MAX_CONCURRENT_RANGE_POSITIONS,
    BASE_MAKER_FEE,
    BASE_TAKER_FEE,
    BASE_SLIPPAGE,
    RANGE_SL_ATR_MULT,
    MIN_TP_DIST_PCT,
    MAX_TP_DIST_PCT,
    RANGE_COOLDOWN_BARS,
    ADX_REGIME_THRESHOLD,
    BB_WIDTH_PERCENTILE_THRESHOLD,
    ENABLE_NEWS_DEFENSE,
    MACRO_LOCKOUT_BEFORE_MINUTES,
    MACRO_LOCKOUT_AFTER_MINUTES,
    PERSISTENT_STATE_FILE,
    LIVE_STATUS_FILE,
    LIVE_MD_FILE,
)
from .regime_classifier import MarketRegimeClassifier, MarketRegime, RegimeData
from .range_engine import (
    RangeMeanReversionEngine,
    RangeActivePosition,
    RangeLimitOrder,
    RangeClosedTrade,
)

logger = logging.getLogger("PaperTraderV32")


def prepare_dual_regime_indicators(df_kline: pd.DataFrame, classifier: MarketRegimeClassifier, lookback: int = DONCHIAN_WINDOW) -> pd.DataFrame:
    """Computes both Trend Donchian channels and Regime Bollinger/Keltner indicators with shift(1) Zero-Lookahead."""
    df = df_kline.copy().sort_values("timestamp").reset_index(drop=True)
    if len(df) == 0:
        return df

    # Prepare Bollinger, Keltner, RSI, ADX, ATR, Volume SMA
    df = classifier.prepare_indicators(df)

    high = df["high"]
    low = df["low"]
    close = df["close"]

    # Trend channels
    df["upper"] = high.shift(1).rolling(lookback).max()
    df["lower"] = low.shift(1).rolling(lookback).min()
    df["ema_fast"] = close.ewm(span=50, adjust=False).mean()
    df["ema_slow"] = close.ewm(span=200, adjust=False).mean()

    return df


class PaperTraderV32:
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        initial_capital: Decimal = INITIAL_CAPITAL,
        base_risk_pct: Decimal = BASE_RISK_PCT,
        max_leverage: Decimal = MAX_LEVERAGE_CAP,
        poll_interval_seconds: int = 5,
        client: Optional[BybitClient] = None,
        bar_interval_ms: int = BAR_INTERVAL_MS,
        state_file: Optional[Path] = None,
        live_status_file: Optional[Path] = None,
        live_md_file: Optional[Path] = None,
        enable_news_defense: bool = ENABLE_NEWS_DEFENSE,
    ):
        self.initial_capital = initial_capital
        self.poll_interval = poll_interval_seconds
        self.client = client or BybitClient(timeout=10, max_retries=3)
        self.bar_interval_ms = bar_interval_ms
        self.state_file = state_file or PERSISTENT_STATE_FILE
        self.live_status_file = live_status_file or LIVE_STATUS_FILE
        self.live_md_file = live_md_file or LIVE_MD_FILE
        self.lock = threading.RLock()
        self.instrument_rules: Dict[str, Dict[str, Decimal]] = {}
        self.enable_news_defense = enable_news_defense

        # News Core v1.2 Defensive Shield
        self.news_config = NewsConfig(
            macro_lockout_before_minutes=MACRO_LOCKOUT_BEFORE_MINUTES,
            macro_lockout_after_minutes=MACRO_LOCKOUT_AFTER_MINUTES,
        )
        self.regime_guard = RegimeGuard(self.news_config) if enable_news_defense else None
        self.macro_blackout_active = False
        self.macro_event_name = ""

        # Screener: Dual-Alpha Architecture (Trend Momentum + IRAS Chop)
        self.screener_config = ScreenerConfig(
            min_turnover_24h=MIN_24H_TURNOVER,
            min_open_interest=MIN_OPEN_INTEREST,
            max_funding_abs=MAX_FUNDING_RATE_ABS,
            min_norm_atr=MIN_NORM_ATR,
            min_adx=MIN_ADX_TREND,
            top_n=TREND_UNIVERSE_TOP_N,
            enable_rvol_filter=ENABLE_RVOL_FILTER,
            min_rvol=MIN_RVOL,
            enable_delta_oi_filter=ENABLE_DELTA_OI_FILTER,
            enable_atr_exhaustion_guard=ENABLE_ATR_EXHAUSTION_GUARD,
            max_daily_atr_exhaustion=MAX_DAILY_ATR_EXHAUSTION,
        )
        self.screener = UniverseScreener(self.screener_config)
        self.trend_screener = self.screener
        self.last_screener_run_ts: float = 0.0
        self.screened_universe: List[ScreenedAsset] = []

        # Chop Screener (IRAS Standard)
        self.chop_screener_config = ChopScreenerConfig(
            min_turnover_24h=CHOP_MIN_24H_TURNOVER,
            min_open_interest=CHOP_MIN_OPEN_INTEREST,
            max_ker=CHOP_MAX_KER,
            min_bandwidth_pct=CHOP_MIN_BANDWIDTH_PCT,
            max_adx=CHOP_MAX_ADX,
            top_n=CHOP_UNIVERSE_TOP_N,
            rescan_interval_hours=CHOP_RESCAN_INTERVAL_HOURS,
        )
        self.chop_screener = ChopUniverseScreener(self.chop_screener_config)
        self.chop_screened_universe: List[ChopCandidateMetric] = []

        # Determine symbols
        initial_syms = list(symbols) if symbols is not None else []
        if not initial_syms and ENABLE_DYNAMIC_UNIVERSE and self.client is not None:
            try:
                top_trend = self.trend_screener.get_top_universe(client=self.client)
                if top_trend:
                    self.screened_universe = self.trend_screener.screen_universe(client=self.client)
                    exclude_for_chop = set(top_trend)
                    self.chop_screened_universe = self.chop_screener.scan_chop_universe(
                        client=self.client,
                        exclude_symbols=exclude_for_chop,
                    )
                    top_chop = [m.symbol for m in self.chop_screened_universe]
                    initial_syms = list(top_trend) + list(top_chop)
                    self.last_screener_run_ts = time.time()
                    logger.info(
                        f"Initialized dual-alpha universe: {len(top_trend)} Trend coins + "
                        f"{len(top_chop)} Chop (IRAS) coins."
                    )
            except Exception as e:
                logger.warning(f"Initial universe screening fallback: {e}")

        if not initial_syms:
            initial_syms = list(DEFAULT_SYMBOLS)

        for anchor in ["BTCUSDT", "USDCUSDT"]:
            if anchor not in initial_syms:
                initial_syms.append(anchor)

        self.symbols = initial_syms
        self._load_instrument_specs()

        # 1. Trend Engine (CoreV3)
        self.trend_engine = CoreV3Engine(
            initial_capital=initial_capital,
            instrument_rules=self.instrument_rules,
            base_risk_pct=base_risk_pct,
            max_leverage=max_leverage,
            taker_fee=BASE_TAKER_FEE,
            slippage=BASE_SLIPPAGE,
            portfolio_leverage=PORTFOLIO_LEVERAGE,
            drawdown_limit_pct=DRAWDOWN_LIMIT_PCT,
            min_adx=float(os.getenv("BOT_MIN_ADX", str(MIN_ADX_TREND))),
            cooldown_bars=3,
            enable_btc_delta_filter=True,
            enable_auto_gearbox=True,
            enable_fast_scratch=True,
            enable_cascade_tp=True,
            bar_interval_ms=self.bar_interval_ms,
        )

        # 2. Range Engine (Maker Mean Reversion Submodule)
        self.range_engine = RangeMeanReversionEngine(
            initial_capital=initial_capital,
            instrument_rules=self.instrument_rules,
            risk_per_trade_pct=RANGE_RISK_PER_TRADE_PCT,
            max_concurrent_positions=MAX_CONCURRENT_RANGE_POSITIONS,
            max_portfolio_leverage=PORTFOLIO_LEVERAGE,
            maker_fee=BASE_MAKER_FEE,
            taker_fee=BASE_TAKER_FEE,
            slippage=BASE_SLIPPAGE,
            sl_atr_mult=RANGE_SL_ATR_MULT,
            min_bandwidth_clearance=MIN_BANDWIDTH_CLEARANCE,
            min_tp_dist_pct=MIN_TP_DIST_PCT,
            max_tp_dist_pct=MAX_TP_DIST_PCT,
            cooldown_bars=RANGE_COOLDOWN_BARS,
        )

        # 3. Regime Classifier
        self.regime_classifier = MarketRegimeClassifier(
            adx_trend_threshold=ADX_REGIME_THRESHOLD,
            bbw_compression_percentile=BB_WIDTH_PERCENTILE_THRESHOLD,
        )

        self.candle_buffers: Dict[str, pd.DataFrame] = {}
        self.last_processed_bar_ts: Dict[str, int] = {s: 0 for s in self.symbols}
        self.prices: Dict[str, Decimal] = {}
        self.price_times: Dict[str, float] = {}
        self.price_server_times: Dict[str, int] = {}
        self.batches: Dict[int, Dict[str, Any]] = {}
        self.ready = False
        self.btc_trend_status = "NEUTRAL"
        self.last_status_cache: Dict[str, Any] = {}
        self.latest_regimes: Dict[str, RegimeData] = {}

        self._load_persistent_state()
        self.stream = MarketStream(self.symbols, interval=CANDLE_INTERVAL)
        self.notifier = TelegramNotifier()

    def _load_instrument_specs(self) -> None:
        try:
            raw_instruments = self.client.get_instruments_info(category="linear")
            for item in raw_instruments:
                sym = item.get("symbol")
                if not sym:
                    continue
                lot = item.get("lotSizeFilter", {})
                price_f = item.get("priceFilter", {})
                self.instrument_rules[sym] = {
                    "minOrderQty": Decimal(str(lot.get("minOrderQty", "0.01"))),
                    "qtyStep": Decimal(str(lot.get("qtyStep", "0.01"))),
                    "tickSize": Decimal(str(price_f.get("tickSize", "0.01"))),
                    "minNotional": Decimal("5.0"),
                    "maxOrderQty": Decimal(str(lot.get("maxOrderQty", "1000000"))),
                }
        except Exception as e:
            logger.warning(f"Failed to fetch instrument rules: {e}")

    def _load_persistent_state(self) -> None:
        if not self.state_file.exists():
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Restore Trend Engine
            trend_data = data.get("trend_engine", {})
            if trend_data:
                self.trend_engine.load_from_dict(trend_data)
            elif "realized_equity" in data:
                self.trend_engine.load_from_dict(data)

            # Restore Range Engine
            range_data = data.get("range_engine", {})
            if range_data:
                self.range_engine.load_dict(range_data)

            runtime = data.get("runtime", {})
            self.last_processed_bar_ts = runtime.get("last_processed_bar_ts", self.last_processed_bar_ts)

            logger.info(
                f"Restored Dual-Regime persistent state: Trend Eq=${self.trend_engine.realized_equity:.2f}, "
                f"Range Eq=${self.range_engine.realized_equity:.2f}, "
                f"Trend Pos={len(self.trend_engine.open_positions)}, Range Pos={len(self.range_engine.open_positions)}"
            )
        except Exception as e:
            logger.error(f"Failed to load persistent state: {e}")

    def save_persistent_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        unified_state = {
            "version": "3.2",
            "schema_version": 5,
            "mode": "DUAL_REGIME_HYBRID",
            "initial_capital": str(self.initial_capital),
            "trend_engine": self.trend_engine.to_dict(),
            "range_engine": self.range_engine.to_dict(),
            "runtime": {
                "last_processed_bar_ts": self.last_processed_bar_ts,
            },
        }
        try:
            atomic_json(self.state_file, unified_state)
        except Exception as e:
            logger.error(f"Failed to save persistent state: {e}")

    def rotate_universe_if_due(self) -> None:
        if not ENABLE_DYNAMIC_UNIVERSE:
            return
        now = time.time()
        if now - self.last_screener_run_ts >= SCREENER_INTERVAL_HOURS * 3600:
            try:
                top_trend = self.trend_screener.get_top_universe(client=self.client)
                if top_trend:
                    self.screened_universe = self.trend_screener.screen_universe(client=self.client)
                    exclude_for_chop = set(top_trend) | set(self.trend_engine.open_positions.keys())

                    if ENABLE_CHOP_SCREENER:
                        self.chop_screened_universe = self.chop_screener.scan_chop_universe(
                            client=self.client,
                            exclude_symbols=exclude_for_chop,
                        )
                        top_chop = [m.symbol for m in self.chop_screened_universe]
                    else:
                        top_chop = []

                    combined = list(top_trend) + list(top_chop)
                    for anchor in ["BTCUSDT", "USDCUSDT"]:
                        if anchor not in combined:
                            combined.append(anchor)

                    new_symbols = list(combined)
                    # Keep held symbols
                    for s in list(self.trend_engine.open_positions.keys()) + list(self.range_engine.open_positions.keys()):
                        if s not in new_symbols:
                            new_symbols.append(s)

                    # Fetch history for newly added symbols so candle buffers are immediately populated
                    if self.client is not None:
                        now_srv = self.client.get_server_time()
                        for s in new_symbols:
                            if s not in self.candle_buffers:
                                try:
                                    raw = self.client.get_kline(s, interval=CANDLE_INTERVAL, limit=500)
                                    records = [
                                        dict(
                                            timestamp=int(k[0]),
                                            **{name: float(k[i]) for i, name in enumerate(("open", "high", "low", "close", "volume", "turnover"), 1)},
                                        )
                                        for k in raw
                                        if int(k[0]) + self.bar_interval_ms <= now_srv
                                    ]
                                    df = (
                                        pd.DataFrame(records)
                                        .sort_values("timestamp")
                                        .drop_duplicates("timestamp")
                                        .reset_index(drop=True)
                                    )
                                    if len(df) >= min(DONCHIAN_WINDOW, 50):
                                        self.candle_buffers[s] = prepare_dual_regime_indicators(df, self.regime_classifier, lookback=DONCHIAN_WINDOW)
                                        self.last_processed_bar_ts[s] = int(df["timestamp"].iloc[-1])
                                except Exception as e_hist:
                                    logger.warning(f"History fetch for new symbol {s} failed: {e_hist}")
                                time.sleep(0.05)

                    self.symbols = new_symbols
                    self._load_instrument_specs()
                    self.trend_engine.instrument_rules = self.instrument_rules
                    self.range_engine.instrument_rules = self.instrument_rules
                    self.stream.update_symbols(new_symbols)
                    logger.info(
                        f"Dynamic Dual-Alpha Universe rotated: {len(top_trend)} Trend + {len(top_chop)} Chop (IRAS) | "
                        f"Total {len(new_symbols)} monitored coins."
                    )
                self.last_screener_run_ts = now
            except Exception as e:
                logger.error(f"Error during dynamic universe rotation: {e}")

    def _fetch_history(self) -> Dict[str, pd.DataFrame]:
        buffers = {}
        now = self.client.get_server_time()
        for sym in self.symbols:
            try:
                raw = self.client.get_kline(sym, interval=CANDLE_INTERVAL, limit=500)
                records = [
                    dict(
                        timestamp=int(k[0]),
                        **{name: float(k[i]) for i, name in enumerate(("open", "high", "low", "close", "volume", "turnover"), 1)},
                    )
                    for k in raw
                    if int(k[0]) + self.bar_interval_ms <= now
                ]
                df = (
                    pd.DataFrame(records)
                    .sort_values("timestamp")
                    .drop_duplicates("timestamp")
                    .reset_index(drop=True)
                )
                if len(df) >= min(DONCHIAN_WINDOW, 50):
                    buffers[sym] = prepare_dual_regime_indicators(df, self.regime_classifier, lookback=DONCHIAN_WINDOW)
            except Exception as e:
                logger.warning(f"Failed to fetch history for {sym}: {e}")
            time.sleep(0.05)
        return buffers

    def get_live_prices(self) -> Dict[str, Decimal]:
        with self.lock:
            now = time.monotonic()
            return {s: p for s, p in self.prices.items() if now - self.price_times.get(s, 0) < 30}

    def _on_bar(self, sym: str, bar: Dict[str, Any]) -> None:
        ts = int(bar["start"])
        previous = self.last_processed_bar_ts.get(sym, 0)
        if ts <= previous:
            return
        if ts != previous + self.bar_interval_ms:
            self.ready = False
            return
        record = dict(
            timestamp=ts,
            **{k: float(bar[k]) for k in ("open", "high", "low", "close", "volume", "turnover")},
        )
        df = self.candle_buffers.get(sym)
        if df is None:
            return
        df = pd.concat(
            [df[["timestamp", "open", "high", "low", "close", "volume", "turnover"]], pd.DataFrame([record])],
            ignore_index=True,
        ).tail(500)
        df = prepare_dual_regime_indicators(df, self.regime_classifier, lookback=DONCHIAN_WINDOW)
        self.candle_buffers[sym] = df
        self.last_processed_bar_ts[sym] = ts
        self.batches.setdefault(ts, {})[sym] = df.iloc[-1].to_dict()

    def _check_macro_embargo(self, current_time: datetime) -> Tuple[bool, str]:
        if not self.enable_news_defense or not self.regime_guard:
            return False, ""
        status = self.regime_guard.check_status("BTCUSDT", current_time=current_time)
        if status.block_new_entries:
            return True, status.reason or "Macro Lockout"
        return False, ""

    def _signals(self) -> None:
        prices = self.get_live_prices()
        now_ms = int(time.time() * 1000)
        for ts, bars in list(self.batches.items()):
            time_since_close = now_ms - (ts + self.bar_interval_ms)
            if time_since_close > 180000:
                del self.batches[ts]
                continue

            # Отказоустойчивый кворум: немедленный вход если все бары на месте,
            # либо запуск по кворуму (≥70% монет включая BTCUSDT) после 30с ожидания
            has_all_bars = set(bars) == set(self.symbols)
            grace_period_expired = time_since_close >= 30000
            has_quorum = ("BTCUSDT" in bars) and (len(bars) >= max(3, int(len(self.symbols) * 0.70)))

            if self.ready and (has_all_bars or (grace_period_expired and has_quorum)):
                # 1. Evaluate BTC trend
                btc_bar = bars.get("BTCUSDT")
                btc_bullish = True
                btc_bearish = True
                if btc_bar:
                    c = float(btc_bar["close"])
                    ef = float(btc_bar.get("ema_fast", 0))
                    es = float(btc_bar.get("ema_slow", 0))
                    btc_bullish = ef > es and c > ef
                    btc_bearish = ef < es and c < ef
                    if btc_bullish:
                        self.btc_trend_status = "BULLISH"
                    elif btc_bearish:
                        self.btc_trend_status = "BEARISH"
                    else:
                        self.btc_trend_status = "NEUTRAL"

                # 2. Classify market regime for each symbol
                regimes_dict = {}
                for s, b in bars.items():
                    reg = self.regime_classifier.classify_bar(
                        symbol=s,
                        adx=float(b.get("adx", 0)),
                        bb_mid=float(b.get("bb_mid", 0)),
                        bb_upper=float(b.get("bb_upper", 0)),
                        bb_lower=float(b.get("bb_lower", 0)),
                        bb_width_pctl=float(b.get("bb_width_pctl", 0.5)),
                        kc_upper=float(b.get("kc_upper", 0)),
                        kc_lower=float(b.get("kc_lower", 0)),
                        rsi=float(b.get("rsi", 50.0)),
                        atr=float(b.get("atr", 0)),
                        volume=float(b.get("volume", 0)),
                        volume_sma=float(b.get("volume_sma", 1.0)),
                    )
                    regimes_dict[s] = reg
                self.latest_regimes = regimes_dict

                # 3. Resilient price map
                open_prices = {s: prices.get(s, Decimal(str(b["close"]))) for s, b in bars.items()}

                # Macro Embargo Check (News Core v1.2)
                now_dt = datetime.now(timezone.utc)
                is_embargo, embargo_event = self._check_macro_embargo(now_dt)
                self.macro_blackout_active = is_embargo
                self.macro_event_name = embargo_event

                if not is_embargo:
                    # 4. Step Range Mean-Reversion Engine (Maker in Chop)
                    active_trend_syms = list(self.trend_engine.open_positions.keys())
                    next_bars_mock = {
                        s: {
                            "open": float(open_prices[s]),
                            "high": float(open_prices[s]),
                            "low": float(open_prices[s]),
                            "close": float(open_prices[s]),
                        }
                        for s, b in bars.items()
                    }
                    self.range_engine.step_bar(
                        timestamp=ts,
                        bars=bars,
                        next_bars=next_bars_mock,
                        next_open_prices=open_prices,
                        regimes=regimes_dict,
                        btc_bullish=btc_bullish,
                        btc_bearish=btc_bearish,
                        active_trend_symbols=active_trend_syms,
                    )

                    # 5. Step Trend Engine (only on symbols NOT held by Range)
                    trend_bars = {
                        s: b for s, b in bars.items()
                        if s not in self.range_engine.open_positions and s not in self.range_engine.pending_orders
                    }
                    self.trend_engine.process_bar_event(
                        timestamp=ts,
                        bars=trend_bars,
                        next_open_prices=open_prices,
                        manage_positions=False,
                        btc_bullish=btc_bullish,
                        btc_bearish=btc_bearish,
                    )
                else:
                    logger.warning(f"Macro Embargo Active ({embargo_event}): Blocked new entries.")

                del self.batches[ts]
                self.rotate_universe_if_due()

    def poll_cycle(self) -> Dict[str, Any]:
        with self.lock:
            prices = self.get_live_prices()
            trend_snap = self.trend_engine.get_portfolio_snapshot(prices)
            range_eq = self.range_engine.get_equity(prices)
            combined_capital = float(self.initial_capital) + float(trend_snap.total_net_pnl) + sum(float(t.net_pnl) for t in self.range_engine.closed_trades)
            combined_equity = float(trend_snap.equity) + (float(range_eq) - float(self.range_engine.initial_capital))

            status_payload = self._export_status(trend_snap, prices, int(time.time() * 1000), combined_equity, combined_capital)
            return status_payload

    def _export_status(
        self,
        trend_snap: PortfolioSnapshot,
        current_prices: Dict[str, Decimal],
        server_ts: int,
        combined_equity: float,
        combined_capital: float,
    ) -> Dict[str, Any]:
        radar = {}
        for sym in self.symbols:
            df = self.candle_buffers.get(sym)
            curr_p = float(current_prices.get(sym, Decimal("0")))
            if df is not None and len(df) > 0 and curr_p > 0:
                last_r = df.iloc[-1]
                up = float(last_r["upper"])
                low = float(last_r["lower"])
                ema_f = float(last_r["ema_fast"])
                ema_s = float(last_r["ema_slow"])
                atr = float(last_r["atr"])
                bb_lo = float(last_r.get("bb_lower", 0))
                bb_up = float(last_r.get("bb_upper", 0))
                bb_mid = float(last_r.get("bb_mid", 0))
                rsi = float(last_r.get("rsi", 50.0))
                adx = float(last_r.get("adx", 0))

                dist_up_pct = max(0.0, (up - curr_p) / curr_p * 100.0)
                dist_low_pct = max(0.0, (curr_p - low) / curr_p * 100.0)
                dist_maker_buy_pct = max(0.0, (curr_p - bb_lo) / curr_p * 100.0) if bb_lo > 0 else 0.0
                dist_maker_sell_pct = max(0.0, (bb_up - curr_p) / curr_p * 100.0) if bb_up > 0 else 0.0

                reg = self.latest_regimes.get(sym)
                reg_name = reg.regime.value if reg else "NEUTRAL"

                in_trend = sym in self.trend_engine.open_positions
                in_range = sym in self.range_engine.open_positions
                in_order = sym in self.range_engine.pending_orders

                if in_trend:
                    status_text = f"TREND {'LONG' if self.trend_engine.open_positions[sym].direction == 1 else 'SHORT'}"
                elif in_range:
                    status_text = f"RANGE {'BUY' if self.range_engine.open_positions[sym].direction == 1 else 'SELL'}"
                elif in_order:
                    status_text = "LIMIT ORDER PENDING"
                else:
                    # Умные информативные статусы для мониторинга причин отсутствия входа
                    if dist_maker_buy_pct == 0.0:
                        if reg_name == "NEUTRAL":
                            status_text = "⚠️ НЕ БОКОВИК (NEUTRAL)"
                        elif self.btc_trend_status == "BEARISH" and sym != "BTCUSDT":
                            status_text = "🛑 БЛОК: BTC BEAR"
                        elif rsi >= 35.0:
                            status_text = f"⏳ RSI {rsi:.1f} (ждёт <35)"
                        else:
                            status_text = "🎯 ГОТОВ К ЛИМИТКЕ"
                    elif dist_up_pct == 0.0:
                        if self.btc_trend_status == "BEARISH" and sym != "BTCUSDT":
                            status_text = "🛑 БЛОК: BTC BEAR (Trend)"
                        else:
                            status_text = "⏳ ЖДЁТ ЗАКРЫТИЯ 1H (LONG)"
                    elif dist_low_pct == 0.0:
                        if self.btc_trend_status == "BULLISH" and sym != "BTCUSDT":
                            status_text = "🛑 БЛОК: BTC BULL (Trend)"
                        else:
                            status_text = "⏳ ЖДЁТ ЗАКРЫТИЯ 1H (SHORT)"
                    elif dist_up_pct <= 1.0 or dist_maker_buy_pct <= 1.0:
                        status_text = "🔥 ИМПУЛЬС (≤1%)"
                    else:
                        status_text = "СКАНИРОВАНИЕ"

                radar[sym] = {
                    "current_price": curr_p,
                    "upper_channel": up,
                    "lower_channel": low,
                    "bb_lower": bb_lo,
                    "bb_upper": bb_up,
                    "bb_mid": bb_mid,
                    "dist_to_long_pct": round(dist_up_pct, 2),
                    "dist_to_short_pct": round(dist_low_pct, 2),
                    "dist_to_maker_buy_pct": round(dist_maker_buy_pct, 2),
                    "dist_to_maker_sell_pct": round(dist_maker_sell_pct, 2),
                    "atr": round(atr, 2),
                    "rsi": round(rsi, 1),
                    "adx": round(adx, 1),
                    "regime": reg_name,
                    "status": status_text,
                }

        bar_sec = int(self.bar_interval_ms / 1000)
        next_candle_sec = bar_sec - (int(server_ts / 1000) % bar_sec)

        # Open positions serializable
        trend_pos = [
            {
                "symbol": p.symbol,
                "direction": "LONG" if p.direction == 1 else "SHORT",
                "qty": float(p.current_qty),
                "entry_price": float(p.entry_price),
                "current_price": float(current_prices.get(p.symbol, p.entry_price)),
                "unrealized_pnl": float((current_prices.get(p.symbol, p.entry_price) - p.entry_price) * p.current_qty * p.direction),
                "stop_loss": float(p.current_stop),
                "module": "TREND",
            }
            for p in self.trend_engine.open_positions.values()
        ]

        range_pos = [
            {
                "symbol": p.symbol,
                "direction": "BUY" if p.direction == 1 else "SELL",
                "qty": float(p.current_qty),
                "entry_price": float(p.entry_price),
                "current_price": float(current_prices.get(p.symbol, p.entry_price)),
                "unrealized_pnl": float((current_prices.get(p.symbol, p.entry_price) - p.entry_price) * p.current_qty * p.direction),
                "take_profit": float(p.take_profit),
                "stop_loss": float(p.stop_loss),
                "module": "RANGE_MAKER",
            }
            for p in self.range_engine.open_positions.values()
        ]

        range_orders = [
            {
                "symbol": o.symbol,
                "direction": "BUY" if o.direction == 1 else "SELL",
                "price": float(o.price),
                "qty": float(o.qty),
                "take_profit": float(o.take_profit),
                "stop_loss": float(o.stop_loss),
            }
            for o in self.range_engine.pending_orders.values()
        ]

        # Closed trades combined
        all_closed = []
        for t in self.trend_engine.closed_trades:
            all_closed.append({
                "symbol": t.symbol,
                "direction": "LONG" if t.direction == 1 else "SHORT",
                "entry_price": float(t.entry_price),
                "exit_price": float(t.exit_price),
                "net_pnl": float(t.net_pnl),
                "exit_reason": t.exit_reason,
                "module": "TREND",
                "exit_time": t.exit_time,
            })
        for t in self.range_engine.closed_trades:
            all_closed.append({
                "symbol": t.symbol,
                "direction": "BUY" if t.direction == 1 else "SELL",
                "entry_price": float(t.entry_price),
                "exit_price": float(t.exit_price),
                "net_pnl": float(t.net_pnl),
                "exit_reason": t.exit_reason,
                "module": "RANGE_MAKER",
                "exit_time": t.exit_time,
            })
        all_closed.sort(key=lambda x: x["exit_time"], reverse=True)

        tot_trades = len(all_closed)
        wins = sum(1 for t in all_closed if t["net_pnl"] > 0)
        win_rate = (wins / tot_trades * 100.0) if tot_trades > 0 else 0.0

        status_data = {
            "version": "3.2",
            "timestamp": server_ts,
            "mode": "DUAL_REGIME_HYBRID",
            "stream_ready": self.ready,
            "monitored_symbols_count": len(self.symbols),
            "btc_trend_status": self.btc_trend_status,
            "datetime_utc": datetime.fromtimestamp(server_ts / 1000, tz=timezone.utc).isoformat(),
            "next_candle_seconds": next_candle_sec,
            "capital": round(combined_capital, 2),
            "portfolio_equity": round(combined_equity, 2),
            "peak_equity": round(max(combined_capital, combined_equity, float(self.trend_engine.peak_equity)), 2),
            "drawdown_pct": round(float(self.trend_engine.max_drawdown_pct), 2),
            "total_trades": tot_trades,
            "win_rate_pct": round(win_rate, 2),
            "open_positions": trend_pos + range_pos,
            "total_open_positions": len(trend_pos) + len(range_pos),
            "trend_open_positions": trend_pos,
            "range_open_positions": range_pos,
            "range_pending_orders": range_orders,
            "closed_trades": all_closed[:50],
            "news_core_status": "v1.2 Shield" if self.enable_news_defense else "Disabled",
            "news_defense_active": bool(self.enable_news_defense),
            "news_shield": bool(self.enable_news_defense),
            "macro_blackout_active": self.macro_blackout_active,
            "market_radar": radar,
            "screened_universe": [
                {
                    "symbol": a.symbol,
                    "composite_score": round(a.composite_score, 2),
                    "relative_strength": round(a.relative_strength, 2),
                    "normalized_atr": round(getattr(a, "norm_atr", getattr(a, "normalized_atr", 0.0)), 4),
                    "funding_rate": round(a.funding_rate, 6),
                }
                for a in self.screened_universe[:25]
            ],
            "chop_screened_universe": [
                m.to_dict() for m in self.chop_screened_universe[:15]
            ],
        }

        self.last_status_cache = status_data

        try:
            self.live_status_file.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(self.live_status_file, status_data)
        except Exception as e:
            logger.warning(f"Failed to write {self.live_status_file}: {e}")

        try:
            self.save_persistent_state()
        except Exception as e:
            logger.warning(f"Failed to save state: {e}")

        return status_data

    def run_daemon(self, max_iterations: Optional[int] = None) -> None:
        logger.info("Connecting to Bybit WebSocket stream for Dual-Regime v3.2...")
        self.stream.start()
        iterations = 0
        last_export = 0.0

        with ThreadPoolExecutor(max_workers=1) as worker:
            history = worker.submit(self._fetch_history)
            try:
                while max_iterations is None or iterations < max_iterations:
                    if self.stream.overflow.is_set():
                        raise RuntimeError("WebSocket queue overflow or rejected subscription")
                    if history is not None and history.done():
                        with self.lock:
                            self.candle_buffers = history.result()
                            for sym, df in self.candle_buffers.items():
                                if len(df) > 0:
                                    self.last_processed_bar_ts[sym] = int(df.iloc[-1]["timestamp"])
                            self.batches.clear()
                            self.ready = len(self.candle_buffers) >= len(self.symbols) * 0.7
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
                            logger.info(f"Dual-Regime v3.2 ready with {len(self.candle_buffers)}/{len(self.symbols)} assets.")
                        history = None

                    try:
                        kind, sym, ts, data, received = self.stream.events.get(timeout=0.2)
                    except queue.Empty:
                        kind = None

                    with self.lock:
                        if kind == "reset":
                            self.ready = False
                            self.prices.clear()
                            self.price_times.clear()
                            self.price_server_times.clear()
                            if history is None:
                                history = worker.submit(self._fetch_history)
                        elif (
                            kind == "price"
                            and time.monotonic() - received < 5
                            and abs(time.time() * 1000 - ts) < 15000
                        ):
                            price = Decimal(str(data))
                            if price.is_finite() and price > 0:
                                self.prices[sym] = price
                                self.price_times[sym] = received
                                self.price_server_times[sym] = ts
                                self.trend_engine.process_price_event(ts, self.get_live_prices())
                        elif kind == "bar" and self.ready:
                            self._on_bar(sym, data)

                        if not self.ready and history is None:
                            history = worker.submit(self._fetch_history)

                        self._signals()

                        if self.trend_engine.events:
                            self.save_persistent_state()
                            for event in self.trend_engine.events:
                                self.notifier.notify(event)
                            self.trend_engine.events.clear()

                        now = time.monotonic()
                        if now - last_export >= self.poll_interval:
                            try:
                                self.poll_cycle()
                            except Exception as e:
                                logger.error(f"PaperTraderV32: Error during poll_cycle: {e}", exc_info=True)
                            last_export = now

                    iterations += 1
            finally:
                self.stream.close()
                self.save_persistent_state()

    def stop(self) -> None:
        self.stream.close()
        self.save_persistent_state()
