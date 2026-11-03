"""
Paper Trading Daemon v3.6 Omega Dual-Regime ZF-8 (Port 8089).

Combines:
1. 8-Speed Intelligent ZF Quant Transmission (G1-G8: 1.0% to 3.6% Risk, Limp Mode at 8% DD, Kickdown + ABS)
2. IRAS Range Mean-Reversion Engine (Chop Maker at Bollinger outer bands, TP at SMA20, SL 1.0 ATR)
3. Dynamic Universe Screeners: Trend Momentum Top 15 + IRAS Chop Top 10
4. News Core v1.2 Defensive Shield: Scheduled Macro Embargo [-45m, +30m] + Emergency Eject
5. Penny-Perfect Shared Equity Accounting Invariant ($300.00 Base)
6. Zero-Lookahead Point-in-Time 1h Execution Protocol
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
from v3.src.core_v3 import ActivePosition, ClosedTrade, PortfolioSnapshot
from v3.src.news_core.news_config import NewsConfig
from v3.src.news_core.regime_guard import RegimeGuard

from v3_2.src.chop_screener import ChopUniverseScreener, ChopScreenerConfig, ChopCandidateMetric
from v3_2.src.regime_classifier import MarketRegimeClassifier, MarketRegime, RegimeData
from v3_2.src.range_engine import (
    RangeMeanReversionEngine,
    RangeActivePosition,
    RangeLimitOrder,
    RangeClosedTrade,
)

from v3_4.src.core_v34 import CoreV34Engine
from v3_4.src.zf8_transmission import ZF8Transmission, GEAR_CONFIGS, GearConfig

from .config import (
    PORT,
    VERSION_NAME,
    DEFAULT_SYMBOLS,
    INITIAL_CAPITAL,
    MAX_LEVERAGE_CAP,
    PORTFOLIO_LEVERAGE,
    DRAWDOWN_LIMIT_PCT,
    CIRCUIT_BREAKER_DD_PCT,
    CIRCUIT_BREAKER_RECOVERY_DD_PCT,
    ATR_STOP_MULT,
    ENABLE_CASCADE_TP,
    MIN_ADX_TREND,
    ENABLE_BTC_DELTA_FILTER,
    ENABLE_FAST_SCRATCH_EXIT,
    FAST_SCRATCH_BARS,
    FAST_SCRATCH_MFE_R,
    REENTRY_COOLDOWN_BARS,
    CANDLE_INTERVAL,
    BAR_INTERVAL_MS,
    DONCHIAN_WINDOW,
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
    RANGE_SL_ATR_MULT,
    MIN_TP_DIST_PCT,
    MAX_TP_DIST_PCT,
    RANGE_COOLDOWN_BARS,
    ADX_REGIME_THRESHOLD,
    BB_WIDTH_PERCENTILE_THRESHOLD,
    BASE_TAKER_FEE,
    BASE_MAKER_FEE,
    BASE_SLIPPAGE,
    ENABLE_NEWS_DEFENSE,
    MACRO_LOCKOUT_BEFORE_MINUTES,
    MACRO_LOCKOUT_AFTER_MINUTES,
    PERSISTENT_STATE_FILE,
    LIVE_STATUS_FILE,
    LIVE_MD_FILE,
)

logger = logging.getLogger("PaperTraderV36")


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

    # Kaufman Efficiency Ratio (KER 14) on shift(1)
    net_chg = (close.shift(1) - close.shift(15)).abs()
    gross_chg = (close.shift(1) - close.shift(2)).abs().rolling(14).sum()
    df["ker"] = (net_chg / gross_chg.replace(0, np.nan)).fillna(0.5).clip(0.0, 1.0)

    return df


class PaperTraderV36:
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        initial_capital: Decimal = INITIAL_CAPITAL,
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

        # Initial symbols
        initial_syms = list(symbols) if symbols is not None else []
        if not initial_syms and ENABLE_DYNAMIC_UNIVERSE and self.client is not None:
            try:
                top_trend = self.trend_screener.get_top_universe(client=self.client)
                if top_trend:
                    self.screened_universe = self.trend_screener.screen_universe(client=self.client)
                    exclude_for_chop = set(top_trend)
                    if ENABLE_CHOP_SCREENER:
                        self.chop_screened_universe = self.chop_screener.scan_chop_universe(
                            client=self.client,
                            exclude_symbols=exclude_for_chop,
                        )
                        top_chop = [m.symbol for m in self.chop_screened_universe]
                    else:
                        top_chop = []
                    initial_syms = list(set(top_trend + top_chop))
            except Exception as e:
                logger.warning(f"Initial dynamic screening failed, using default: {e}")

        if not initial_syms:
            initial_syms = list(DEFAULT_SYMBOLS)

        for anchor in ["BTCUSDT", "ETHUSDT"]:
            if anchor not in initial_syms:
                initial_syms.append(anchor)

        self.symbols = initial_syms
        self._load_instrument_specs()

        # 1. Trend Engine (CoreV34Engine with 8-Speed ZF Transmission)
        self.trend_engine = CoreV34Engine(
            initial_capital=initial_capital,
            instrument_rules=self.instrument_rules,
            max_leverage=MAX_LEVERAGE_CAP,
            atr_stop_mult=ATR_STOP_MULT,
            taker_fee=BASE_TAKER_FEE,
            slippage=BASE_SLIPPAGE,
            portfolio_leverage=PORTFOLIO_LEVERAGE,
            drawdown_limit_pct=DRAWDOWN_LIMIT_PCT,
            min_adx=float(os.getenv("BOT_MIN_ADX", str(MIN_ADX_TREND))),
            cooldown_bars=REENTRY_COOLDOWN_BARS,
            enable_btc_delta_filter=ENABLE_BTC_DELTA_FILTER,
            enable_fast_scratch=ENABLE_FAST_SCRATCH_EXIT,
            fast_scratch_bars=FAST_SCRATCH_BARS,
            fast_scratch_mfe_r=FAST_SCRATCH_MFE_R,
            enable_cascade_tp=ENABLE_CASCADE_TP,
            bar_interval_ms=self.bar_interval_ms,
        )

        # 2. Range Engine (IRAS Range Mean-Reversion Maker)
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
        self.batches: Dict[int, Dict[str, Any]] = {}
        self.ready = False
        self.btc_trend_status = "NEUTRAL"
        self.last_status_cache: Dict[str, Any] = {
            "version": VERSION_NAME,
            "port": PORT,
            "equity": float(self.initial_capital),
            "capital": float(self.initial_capital),
            "portfolio_equity": float(self.initial_capital),
            "initial_capital": float(self.initial_capital),
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "max_drawdown_pct": 0.0,
            "total_open_positions": 0,
            "open_positions": {},
            "trend_open_positions": {},
            "range_open_positions": {},
            "closed_trades": [],
            "btc_trend_status": "NEUTRAL",
            "stream_ready": True,
            "bybit_ws_connected": True,
            "gear": 1,
            "gear_name": "LIMP / CRAWL",
            "gear_risk_pct": 1.0,
            "gear_pyramiding": False,
            "limp_mode": False,
            "news_core_status": "v1.2 Shield",
            "news_defense_active": True,
            "news_shield": True,
            "radar": {},
        }
        self.latest_regimes: Dict[str, RegimeData] = {}
        self.macro_blackout_active: bool = False
        self.macro_event_name: str = ""

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

            trend_data = data.get("trend_engine", {})
            if trend_data:
                self.trend_engine.load_from_dict(trend_data)
            elif "realized_equity" in data:
                self.trend_engine.load_from_dict(data)

            range_data = data.get("range_engine", {})
            if range_data:
                self.range_engine.load_dict(range_data)

            runtime = data.get("runtime", {})
            self.last_processed_bar_ts = runtime.get("last_processed_bar_ts", self.last_processed_bar_ts)

            logger.info(
                f"Restored Omega persistent state: Trend Eq=${self.trend_engine.realized_equity:.2f}, "
                f"Range Eq=${self.range_engine.realized_equity:.2f}, "
                f"Gear={self.trend_engine.transmission.current_gear}"
            )
        except Exception as e:
            logger.error(f"Failed to load persistent state: {e}")

    def save_persistent_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        unified_state = {
            "version": "3.6",
            "schema_version": 6,
            "mode": "OMEGA_DUAL_REGIME_ZF8",
            "initial_capital": str(self.initial_capital),
            "trend_engine": self.trend_engine.to_dict(),
            "range_engine": self.range_engine.to_dict(),
            "gear": self.trend_engine.transmission.current_gear,
            "gear_config": {
                k: (float(v) if isinstance(v, Decimal) else v)
                for k, v in self.trend_engine.transmission.current_config.__dict__.items()
            },
            "transmission_telemetry": self.trend_engine.transmission.to_dict(),
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
                    for anchor in ["BTCUSDT", "ETHUSDT"]:
                        if anchor not in combined:
                            combined.append(anchor)

                    new_symbols = list(combined)
                    for s in list(self.trend_engine.open_positions.keys()) + list(self.range_engine.open_positions.keys()):
                        if s not in new_symbols:
                            new_symbols.append(s)

                    self.symbols = new_symbols
                    self._load_instrument_specs()
                    self.trend_engine.instrument_rules = self.instrument_rules
                    self.range_engine.instrument_rules = self.instrument_rules
                    self.stream.update_symbols(new_symbols)
                    logger.info(
                        f"Dynamic Omega Universe rotated: {len(top_trend)} Trend + {len(top_chop)} Chop (IRAS) | "
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

    def _check_macro_embargo(self, current_dt: datetime) -> Tuple[bool, str]:
        if not self.enable_news_defense or not self.regime_guard:
            return False, ""
        status = self.regime_guard.check_status("BTCUSDT", current_time=current_dt)
        return status.block_new_entries, status.reason or ""

    def _signals(self) -> None:
        prices = self.get_live_prices()
        now_ms = int(time.time() * 1000)
        now_dt = datetime.now(timezone.utc)
        
        is_embargo, embargo_event = self._check_macro_embargo(now_dt)
        self.macro_blackout_active = is_embargo
        self.macro_event_name = embargo_event

        for ts, bars in list(self.batches.items()):
            time_since_close = now_ms - (ts + self.bar_interval_ms)
            if time_since_close > 180000:
                del self.batches[ts]
                continue

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
                    ker = float(b.get("ker", 0.10))
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
                    # IRAS filter: if KER > 0.15, force neutral/trend
                    if ker > 0.15:
                        reg.is_chop = False
                        reg.regime = MarketRegime.NEUTRAL
                    regimes_dict[s] = reg
                self.latest_regimes = regimes_dict

                open_prices = {s: prices.get(s, Decimal(str(b["close"]))) for s, b in bars.items()}

                # Macro Embargo Gate
                if not is_embargo:
                    # 3. Step Range Mean-Reversion Engine (Maker in Chop)
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

                    # 4. Step Trend Engine (only on symbols NOT held by Range)
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
                dist_l = ((up - curr_p) / curr_p) * 100.0 if up > 0 else 0.0
                dist_s = ((curr_p - low) / curr_p) * 100.0 if low > 0 else 0.0
                reg_d = self.latest_regimes.get(sym)
                reg_str = reg_d.regime.value if reg_d else "NEUTRAL"
                radar[sym] = {
                    "current_price": curr_p,
                    "upper_channel": up,
                    "lower_channel": low,
                    "dist_to_long_pct": round(dist_l, 2),
                    "dist_to_short_pct": round(dist_s, 2),
                    "trend": "BULLISH" if ema_f > ema_s else "BEARISH",
                    "adx": round(float(last_r.get("adx", 0)), 1),
                    "rsi": round(float(last_r.get("rsi", 50)), 1),
                    "ker": round(float(last_r.get("ker", 0.1)), 2),
                    "regime": reg_str,
                }

        trend_positions = {}
        for sym, pos in self.trend_engine.open_positions.items():
            curr_p = float(current_prices.get(sym, pos.entry_price))
            direction_str = "LONG" if pos.direction == 1 else "SHORT"
            unrealized = float((Decimal(str(curr_p)) - pos.entry_price) * Decimal(str(pos.direction)) * pos.current_qty)
            trend_positions[sym] = {
                "symbol": sym,
                "direction": direction_str,
                "current_qty": float(pos.current_qty),
                "entry_price": float(pos.entry_price),
                "current_price": curr_p,
                "current_stop": float(pos.current_stop),
                "unrealized_pnl": round(unrealized, 2),
                "entry_time": pos.entry_timestamp,
                "is_pyramided": pos.is_pyramided,
                "tp1_hit": pos.tp1_hit,
                "tp2_hit": pos.tp2_hit,
                "mode": "TREND_BREAKOUT",
            }

        range_positions = {}
        for sym, pos in self.range_engine.open_positions.items():
            curr_p = float(current_prices.get(sym, pos.entry_price))
            unrealized = float(pos.compute_unrealized_pnl(Decimal(str(curr_p))))
            range_positions[sym] = {
                "symbol": sym,
                "direction": "LONG" if pos.side.value == "BUY" else "SHORT",
                "current_qty": float(pos.qty),
                "entry_price": float(pos.entry_price),
                "current_price": curr_p,
                "current_stop": float(pos.stop_loss),
                "take_profit": float(pos.take_profit),
                "unrealized_pnl": round(unrealized, 2),
                "entry_time": pos.entry_timestamp,
                "mode": "IRAS_RANGE_MAKER",
            }

        all_open_positions = {**trend_positions, **range_positions}

        gear = self.trend_engine.transmission.current_gear
        g_cfg = self.trend_engine.transmission.current_config

        closed_trend_list = [
            {
                "symbol": t.symbol,
                "direction": "LONG" if t.direction == 1 else "SHORT",
                "entry_price": float(t.entry_price),
                "exit_price": float(t.exit_price),
                "net_pnl": float(t.net_pnl),
                "exit_reason": t.exit_reason,
                "exit_time": t.exit_timestamp,
                "mode": "TREND",
            }
            for t in self.trend_engine.closed_trades[-30:]
        ]

        closed_range_list = [
            {
                "symbol": t.symbol,
                "direction": "LONG" if t.side.value == "BUY" else "SHORT",
                "entry_price": float(t.entry_price),
                "exit_price": float(t.exit_price),
                "net_pnl": float(t.net_pnl),
                "exit_reason": t.exit_reason,
                "exit_time": t.exit_timestamp,
                "mode": "RANGE",
            }
            for t in self.range_engine.closed_trades[-30:]
        ]

        all_closed = sorted(closed_trend_list + closed_range_list, key=lambda x: x["exit_time"], reverse=True)

        payload = {
            "version": VERSION_NAME,
            "port": PORT,
            "timestamp": server_ts,
            "equity": round(combined_equity, 2),
            "capital": round(combined_capital, 2),
            "portfolio_equity": round(combined_equity, 2),
            "initial_capital": float(self.initial_capital),
            "realized_pnl": round(combined_capital - float(self.initial_capital), 2),
            "unrealized_pnl": round(combined_equity - combined_capital, 2),
            "total_open_positions": len(all_open_positions),
            "open_positions": all_open_positions,
            "trend_open_positions": trend_positions,
            "range_open_positions": range_positions,
            "range_pending_orders": len(self.range_engine.pending_orders),
            "closed_trades": all_closed,
            "btc_trend_status": self.btc_trend_status,
            "stream_ready": bool(self.ready),
            "bybit_ws_connected": bool(self.ready),
            "monitored_symbols_count": len(self.symbols),
            "gear": gear,
            "gear_name": g_cfg.name,
            "gear_risk_pct": float(g_cfg.risk_pct * 100),
            "gear_pyramiding": g_cfg.pyramiding_allowed,
            "limp_mode": bool(self.trend_engine.transmission.is_circuit_breaker_active),
            "news_core_status": "v1.2 Shield" if self.enable_news_defense else "Disabled",
            "news_defense_active": bool(self.enable_news_defense),
            "news_shield": bool(self.enable_news_defense),
            "macro_blackout_active": self.macro_blackout_active,
            "macro_event_name": self.macro_event_name,
            "max_drawdown_pct": round(float(self.trend_engine.max_drawdown_pct), 2),
            "radar": radar,
        }
        self.last_status_cache = payload
        return payload

    def run_daemon(self, max_iterations: Optional[int] = None) -> None:
        logger.info(f"Connecting to Bybit WebSocket stream for {VERSION_NAME}...")
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
                            self.ready = len(self.candle_buffers) >= max(3, int(len(self.symbols) * 0.7))
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
                            logger.info(f"{VERSION_NAME} ready with {len(self.candle_buffers)}/{len(self.symbols)} assets.")
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
                            status = self.poll_cycle()
                            atomic_json(self.live_status_file, status)
                            self.save_persistent_state()
                            last_export = now
                            iterations += 1

            except Exception as e:
                logger.error(f"Exception in paper trader loop: {e}", exc_info=True)
                time.sleep(2.0)

    def _on_price(self, sym: str, price: Decimal) -> None:
        with self.lock:
            self.prices[sym] = price
            self.price_times[sym] = time.monotonic()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    pt = PaperTraderV36()
    pt.run_daemon()
