"""
Single-writer Paper Trading Daemon v3 on Public Bybit WebSocket.
Enhanced with:
1. Dynamic Universe Selection (Bybit 400+ Linear Perpetuals ranked by Composite Momentum Score)
2. Smooth Universe Rotation every 4 hours (active positions guaranteed never dropped)
3. 4-Hour Donchian Breakout with ADX trend filter and BTC Delta alignment
4. Anti-martingale pyramiding with weighted average entry price
5. News Core v1.1 Defensive Shield (Emergency Eject, Squeeze Escape, Runner Protection, Regime Guard)
6. Atomic crash-resistant persistence (BOT_STATE_PERSISTENT_V3.json)
7. Dedicated Web Monitor on Port 8082
"""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import queue
import sys
import threading
import time
from typing import Dict, List, Optional, Any, Set
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASE_DIR = Path(__file__).resolve().parent.parent
for p in [str(REPO_ROOT), str(BASE_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from v3.src.bybit_client import BybitClient
from v3.src.config import (
    REPORTS_DIR,
    PERSISTENT_STATE_FILE,
    LIVE_STATUS_FILE,
    LIVE_MD_FILE,
    BASE_TAKER_FEE,
    BASE_SLIPPAGE,
    DEFAULT_SYMBOLS,
    INITIAL_CAPITAL,
    BASE_RISK_PCT,
    PORTFOLIO_LEVERAGE,
    DRAWDOWN_LIMIT_PCT,
    SOFT_DRAWDOWN_LIMIT_PCT,
    REENTRY_COOLDOWN_BARS,
    ENABLE_BTC_DELTA_FILTER,
    CANDLE_INTERVAL,
    BAR_INTERVAL_MS,
    DONCHIAN_WINDOW,
    MIN_ADX_TREND,
    ENABLE_DYNAMIC_UNIVERSE,
    UNIVERSE_TOP_N,
    SCREENER_INTERVAL_HOURS,
    MIN_24H_TURNOVER,
    MIN_OPEN_INTEREST,
    MAX_FUNDING_RATE_ABS,
    MIN_NORM_ATR,
    ENABLE_NEWS_DEFENSE,
    ENABLE_MACRO_BLACKOUT,
    ENABLE_DEPEG_GUARD,
    ENABLE_EMERGENCY_EJECT,
    ENABLE_SQUEEZE_ESCAPE,
    ENABLE_RUNNER_PROTECTION,
    ENABLE_CASCADE_TP,
    ENABLE_FAST_SCRATCH_EXIT,
    ENABLE_AUTO_GEARBOX,
    ENABLE_RVOL_FILTER,
    MIN_RVOL,
    ENABLE_DELTA_OI_FILTER,
    ENABLE_ATR_EXHAUSTION_GUARD,
    MAX_DAILY_ATR_EXHAUSTION,
)
from v3.src.core_v3 import CoreV3Engine, PortfolioSnapshot, ActivePosition
from v3.src.market_stream import MarketStream
from v3.src.universe_screener import UniverseScreener, ScreenerConfig, ScreenedAsset
from v3.src.news_core import (
    NewsCore,
    NewsConfig,
    PositionView,
    PositionAction,
    Side,
    ActionType,
    EventType,
)
from v3.src.persistence import atomic_json
from v3.src.indicators import calculate_adx
from v3.src.telegram_notifier import TelegramNotifier

logger = logging.getLogger("PaperTraderV3")


def prepare_symbol_indicators(df_kline: pd.DataFrame, lookback: int = DONCHIAN_WINDOW, atr_period: int = 14) -> pd.DataFrame:
    """Prepares indicators with zero-lookahead PIT (shift(1) on closed bars)."""
    df = df_kline.copy().sort_values("timestamp").reset_index(drop=True)
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    df["upper"] = high.shift(1).rolling(lookback).max()
    df["lower"] = low.shift(1).rolling(lookback).min()

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1.0 / atr_period, adjust=False).mean()

    df["ema_fast"] = close.ewm(span=50, adjust=False).mean()
    df["ema_slow"] = close.ewm(span=200, adjust=False).mean()
    df["volume_sma"] = volume.rolling(20).mean()
    df["adx"] = calculate_adx(df)
    return df


class PaperTraderV3:
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        initial_capital: Decimal = INITIAL_CAPITAL,
        base_risk_pct: Decimal = BASE_RISK_PCT,
        max_leverage: Decimal = Decimal("3.5"),
        poll_interval_seconds: int = 5,
        client: Optional[BybitClient] = None,
        bar_interval_ms: int = BAR_INTERVAL_MS,
    ):
        self.initial_capital = initial_capital
        self.poll_interval = poll_interval_seconds
        self.client = client or BybitClient(timeout=10, max_retries=3)
        self.bar_interval_ms = bar_interval_ms
        self.lock = threading.RLock()
        self.instrument_rules: Dict[str, Dict[str, Decimal]] = {}
        self.screener_config = ScreenerConfig(
            min_turnover_24h=MIN_24H_TURNOVER,
            min_open_interest=MIN_OPEN_INTEREST,
            max_funding_abs=MAX_FUNDING_RATE_ABS,
            min_norm_atr=MIN_NORM_ATR,
            min_adx=MIN_ADX_TREND,
            top_n=UNIVERSE_TOP_N,
            enable_rvol_filter=ENABLE_RVOL_FILTER,
            min_rvol=MIN_RVOL,
            enable_delta_oi_filter=ENABLE_DELTA_OI_FILTER,
            enable_atr_exhaustion_guard=ENABLE_ATR_EXHAUSTION_GUARD,
            max_daily_atr_exhaustion=MAX_DAILY_ATR_EXHAUSTION,
        )
        self.screener = UniverseScreener(self.screener_config)
        self.last_screener_run_ts: float = 0.0
        self.screened_universe: List[ScreenedAsset] = []

        # Determine initial symbols
        initial_syms = list(symbols) if symbols is not None else []
        if not initial_syms and ENABLE_DYNAMIC_UNIVERSE and self.client is not None:
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
            initial_syms = list(DEFAULT_SYMBOLS)

        # Ensure anchor coins BTCUSDT and USDCUSDT (for depeg guard) are always monitored
        for anchor in ["BTCUSDT", "USDCUSDT"]:
            if anchor not in initial_syms:
                initial_syms.append(anchor)

        self.symbols = initial_syms
        self._load_instrument_specs()

        self.engine = CoreV3Engine(
            initial_capital=initial_capital,
            instrument_rules=self.instrument_rules,
            base_risk_pct=base_risk_pct,
            max_leverage=max_leverage,
            taker_fee=BASE_TAKER_FEE,
            slippage=BASE_SLIPPAGE,
            portfolio_leverage=PORTFOLIO_LEVERAGE,
            drawdown_limit_pct=DRAWDOWN_LIMIT_PCT,
            min_adx=float(os.getenv("BOT_MIN_ADX", str(MIN_ADX_TREND))),
            cooldown_bars=REENTRY_COOLDOWN_BARS,
            enable_btc_delta_filter=ENABLE_BTC_DELTA_FILTER,
            enable_auto_gearbox=ENABLE_AUTO_GEARBOX,
            enable_fast_scratch=ENABLE_FAST_SCRATCH_EXIT,
            enable_cascade_tp=ENABLE_CASCADE_TP,
        )

        self.state_file = PERSISTENT_STATE_FILE
        self.candle_buffers: Dict[str, pd.DataFrame] = {}
        self.last_processed_bar_ts: Dict[str, int] = {s: 0 for s in self.symbols}
        self.prices: Dict[str, Decimal] = {}
        self.price_times: Dict[str, float] = {}
        self.price_server_times: Dict[str, int] = {}
        self.daily_date = ""
        self.batches: Dict[int, Dict[str, Any]] = {}
        self.ready = False
        self.btc_trend_status = "NEUTRAL"
        self.last_status_cache: Dict[str, Any] = {}

        self._load_persistent_state()
        self.stream = MarketStream(self.symbols, interval=CANDLE_INTERVAL)
        self.notifier = TelegramNotifier()

        # News Core Defensive Shield
        if ENABLE_NEWS_DEFENSE:
            cfg = NewsConfig.defensive_only()
            cfg.enable_emergency_eject = ENABLE_EMERGENCY_EJECT
            cfg.enable_squeeze_escape = ENABLE_SQUEEZE_ESCAPE
            cfg.enable_super_runner = True
            cfg.protect_runners_min_r = 1.5 if ENABLE_RUNNER_PROTECTION else 999.0
            cfg.enable_stablecoin_guard = ENABLE_DEPEG_GUARD
            self.news_core = NewsCore(cfg)
            logger.info("News Core Defensive Shield initialized (defensive_only profile).")
        else:
            self.news_core = None

        self.recent_news_events = deque(maxlen=25)
        self.recent_news_events.append({
            "timestamp": int(time.time() * 1000),
            "headline": "News Core v1.1 Defensive Shield active. Regime Guard monitoring macro & depeg risks.",
            "symbol": "MARKET",
            "event_type": "SHIELD_INIT",
            "score": 0,
            "actions_taken": ["MONITOR"],
            "impact": "DEFENSE_READY",
        })

    def _load_instrument_specs(self) -> None:
        """Fetches Bybit market specifications with full pagination for current symbols."""
        try:
            for item in self.client.get_instruments_info(category="linear"):
                sym = item.get("symbol")
                if (
                    sym not in self.symbols
                    or item.get("status") != "Trading"
                    or item.get("settleCoin") != "USDT"
                    or item.get("contractType") != "LinearPerpetual"
                ):
                    continue
                lot = item.get("lotSizeFilter", {})
                self.instrument_rules[sym] = {
                    "minOrderQty": Decimal(str(lot.get("minOrderQty", "0.01"))),
                    "qtyStep": Decimal(str(lot.get("qtyStep", "0.01"))),
                    "minNotional": Decimal(str(lot.get("minNotionalValue", "5.0"))),
                    "maxOrderQty": Decimal(str(lot.get("maxMktOrderQty", "1000000"))),
                    "tickSize": Decimal(str(item.get("priceFilter", {}).get("tickSize", "0.01"))),
                }
        except Exception as e:
            logger.warning(f"Error fetching instrument specs: {e}")

        missing = set(self.symbols) - set(self.instrument_rules)
        if missing:
            logger.warning(f"Unverified instruments: {sorted(missing)}. Using fallback rules.")
            for sym in missing:
                self.instrument_rules[sym] = {
                    "minOrderQty": Decimal("0.01"),
                    "qtyStep": Decimal("0.01"),
                    "minNotional": Decimal("5.0"),
                    "maxOrderQty": Decimal("1000000"),
                    "tickSize": Decimal("0.01"),
                }

    def _load_persistent_state(self) -> None:
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                self.engine.load_from_dict(data)
                runtime = data.get("runtime", {})
                self.last_processed_bar_ts.update(runtime.get("last_processed_bar_ts", {}))
                self.daily_date = runtime.get("daily_date", "")
                logger.info(
                    f"Restored persistent state v3: equity=${self.engine.realized_equity:.2f}, "
                    f"open_positions={list(self.engine.open_positions.keys())}"
                )
            except Exception as e:
                logger.error(f"Failed to load persistent snapshot: {e}")
                raise

    def save_persistent_state(self) -> None:
        with self.lock:
            data = self.engine.to_dict()
            data["runtime"] = {
                "last_processed_bar_ts": self.last_processed_bar_ts,
                "daily_date": self.daily_date,
                "monitored_symbols": self.symbols,
            }
            try:
                atomic_json(self.state_file, data)
            except Exception as e:
                logger.error(f"PaperTraderV3: Failed to save persistent state: {e}")

    def rotate_universe_if_due(self) -> None:
        """
        Executes dynamic universe rotation if interval has passed.
        Smoothly rotates: open positions are NEVER dropped from subscription.
        """
        if not ENABLE_DYNAMIC_UNIVERSE:
            return

        now = time.time()
        if now - self.last_screener_run_ts < SCREENER_INTERVAL_HOURS * 3600:
            return

        with self.lock:
            try:
                active_pos_symbols = list(self.engine.open_positions.keys())
                new_symbols = self.screener.get_top_universe(
                    client=self.client,
                    keep_active_symbols=active_pos_symbols,
                )
                if not new_symbols:
                    return

                # Ensure BTC and USDC are always monitored
                for anchor in ["BTCUSDT", "USDCUSDT"]:
                    if anchor not in new_symbols:
                        new_symbols.append(anchor)

                added = set(new_symbols) - set(self.symbols)
                removed = set(self.symbols) - set(new_symbols)

                if added or removed:
                    logger.info(
                        f"🔄 Universe Rotation: +{len(added)} added {sorted(added)}, "
                        f"-{len(removed)} removed {sorted(removed)}"
                    )
                    self.symbols = new_symbols
                    self._load_instrument_specs()
                    self.engine.instrument_rules = self.instrument_rules
                    self.stream.update_symbols(new_symbols)

                    # Update screener universe data
                    self.screened_universe = self.screener.screen_universe(client=self.client)

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
                if len(df) >= 30:
                    buffers[sym] = prepare_symbol_indicators(df, lookback=DONCHIAN_WINDOW)
            except Exception as e:
                logger.warning(f"Failed to fetch history for {sym}: {e}")
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
        df = prepare_symbol_indicators(df, lookback=DONCHIAN_WINDOW)
        self.candle_buffers[sym] = df
        self.last_processed_bar_ts[sym] = ts
        self.batches.setdefault(ts, {})[sym] = df.iloc[-1].to_dict()

    def _signals(self) -> None:
        prices = self.get_live_prices()
        now_ms = int(time.time() * 1000)
        for ts, bars in list(self.batches.items()):
            if now_ms - (ts + self.bar_interval_ms) > 120000:
                del self.batches[ts]
                continue
            if (
                set(bars) == set(self.symbols)
                and set(prices) == set(self.symbols)
                and self.ready
                and all(self.price_server_times.get(s, 0) >= ts + self.bar_interval_ms for s in self.symbols)
            ):
                # Evaluate BTC trend
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

                # Check News Core Regime Guard blocks
                blocked_symbols = set()
                nc = getattr(self, "news_core", None)
                if nc:
                    bar_dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
                    for s in self.symbols:
                        st = nc.get_status(s, bar_dt)
                        if st.get("block_new_entries"):
                            blocked_symbols.add(s)
                            logger.info(
                                f"🛡️ Regime Guard: Entry blocked for {s} at {bar_dt} ({st.get('reason')})"
                            )

                self.engine.process_bar_event(
                    timestamp=ts,
                    bars=bars,
                    next_open_prices=prices,
                    manage_positions=False,
                    btc_bullish=btc_bullish,
                    btc_bearish=btc_bearish,
                    blocked_symbols=blocked_symbols,
                )
                del self.batches[ts]

                # Check if dynamic rotation is due
                self.rotate_universe_if_due()

    def poll_cycle(self) -> PortfolioSnapshot:
        with self.lock:
            prices = self.get_live_prices()
            snap = self.engine.get_portfolio_snapshot(prices)
            try:
                self._export_status(snap, prices, int(time.time() * 1000))
            except Exception as e:
                logger.warning(f"PaperTraderV3: Error during status export: {e}")
            return snap

    def handle_news_event(
        self,
        headline: str,
        symbol: Optional[str] = None,
        source_tier: int = 3,
        ret_1m: float = 0.0,
        vol_1m: float = 0.0,
        vol_median_60m: float = 1.0,
        timestamp: Optional[datetime] = None,
    ) -> List[PositionAction]:
        """
        Receives breaking news, classifies urgency, and applies defensive shields.
        Executes immediate market close on Emergency Eject / Squeeze Escape,
        or trails stop loss for Runner Protection / Soft Defense.
        """
        nc = getattr(self, "news_core", None)
        if not nc:
            return []

        now_dt = timestamp or datetime.now(timezone.utc)
        target_sym = symbol or nc.extract_symbol(headline, default="BTCUSDT")

        with self.lock:
            event_type, score = nc.classify(
                headline, symbol=target_sym, timestamp=now_dt
            )
            if score == 0:
                logger.debug(f"News event ignored (neutral score 0): {headline}")
                return []

            logger.info(
                f"News Core Headline received for {target_sym}: event={event_type.value}, "
                f"score={score}, tier={source_tier} -> '{headline[:80]}...'"
            )

            if not hasattr(self, "recent_news_events"):
                self.recent_news_events = deque(maxlen=25)

            event_record = {
                "timestamp": int(now_dt.timestamp() * 1000),
                "headline": headline,
                "symbol": target_sym,
                "event_type": event_type.value if hasattr(event_type, "value") else str(event_type),
                "score": score,
                "actions_taken": ["MONITOR"],
                "impact": "MONITORED_NO_POSITION" if target_sym not in self.engine.open_positions else "EVALUATING",
            }
            self.recent_news_events.appendleft(event_record)

            if target_sym not in self.engine.open_positions:
                return []

            pos = self.engine.open_positions[target_sym]
            mark_price = self.prices.get(target_sym, pos.entry_price)
            curr_p = float(mark_price)
            entry_p = float(pos.entry_price)
            stop_p = float(pos.current_stop)

            risk_dist = abs(entry_p - stop_p)
            r_mult = ((curr_p - entry_p) / risk_dist) if (pos.direction == 1 and risk_dist > 0) else (
                ((entry_p - curr_p) / risk_dist) if risk_dist > 0 else 0.0
            )

            pos_view = PositionView(
                symbol=target_sym,
                side=Side.LONG if pos.direction == 1 else Side.SHORT,
                size=float(pos.current_qty),
                entry_price=entry_p,
                current_price=curr_p,
                stop_loss=stop_p,
                unrealized_pnl_r=r_mult,
            )

            actions = nc.manage_positions(
                symbol=target_sym,
                score=score,
                event=event_type,
                open_positions={target_sym: pos_view},
                current_price=curr_p,
                timestamp=now_dt,
                ret_1m=ret_1m,
                vol_1m=vol_1m,
                vol_median_60m=vol_median_60m,
                source_tier=source_tier,
            )

            now_ms = int(now_dt.timestamp() * 1000)
            executed_actions = []

            for act in actions:
                if act.action_type == ActionType.CLOSE_MARKET:
                    logger.warning(
                        f"🛡️ NEWS SHIELD: Emergency Market Close for {act.symbol} ({act.reason})"
                    )
                    self.engine.emergency_close_position(
                        symbol=act.symbol,
                        current_price=mark_price,
                        timestamp=now_ms,
                        reason=f"NEWS_{act.reason}",
                    )
                    self.notifier.notify(
                        dict(
                            kind="NEWS_EMERGENCY_EJECT",
                            symbol=act.symbol,
                            reason=act.reason,
                            price=str(mark_price),
                            headline=headline[:100],
                        )
                    )
                    self.save_persistent_state()
                    executed_actions.append(act)

                elif act.action_type == ActionType.MODIFY_STOP_TP:
                    if act.stop_price is not None:
                        new_stop = Decimal(str(act.stop_price))
                        logger.info(
                            f"🛡️ NEWS SHIELD: Trailing stop for {act.symbol} to {new_stop} ({act.reason})"
                        )
                        self.engine.update_position_stop(
                            symbol=act.symbol,
                            new_stop=new_stop,
                            reason=f"NEWS_{act.reason}",
                        )
                        self.notifier.notify(
                            dict(
                                kind="NEWS_STOP_UPDATE",
                                symbol=act.symbol,
                                new_stop=str(new_stop),
                                reason=act.reason,
                                headline=headline[:100],
                            )
                        )
                        self.save_persistent_state()
                        executed_actions.append(act)

            if executed_actions:
                event_record["actions_taken"] = [f"{act.action_type.name}:{act.reason}" for act in executed_actions]
                event_record["impact"] = "SHIELD_DEFENSE_EXECUTED"

            return executed_actions

    def run_daemon(self, max_iterations: Optional[int] = None) -> None:
        self.stream.start()
        iterations = 0
        last_export = 0
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
                                self.last_processed_bar_ts[sym] = int(df.timestamp.iloc[-1])
                            self.batches.clear()
                            self.ready = True
                            btc_df = self.candle_buffers.get("BTCUSDT")
                            if btc_df is not None and len(btc_df) > 0:
                                last = btc_df.iloc[-1]
                                c = float(last["close"])
                                ef = float(last["ema_fast"])
                                es = float(last["ema_slow"])
                                if ef > es and c > ef:
                                    self.btc_trend_status = "BULLISH"
                                elif ef < es and c < ef:
                                    self.btc_trend_status = "BEARISH"
                                else:
                                    self.btc_trend_status = "NEUTRAL"
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
                                nc = getattr(self, "news_core", None)
                                if nc and sym == "USDCUSDT":
                                    nc.regime_guard.update_stablecoin_price(float(price))
                                self.engine.process_price_event(ts, self.get_live_prices())
                        elif kind == "bar" and self.ready:
                            self._on_bar(sym, data)

                        if not self.ready and history is None:
                            history = worker.submit(self._fetch_history)

                        self._signals()

                        if self.engine.events:
                            self.save_persistent_state()
                            for event in self.engine.events:
                                self.notifier.notify(event)
                            self.engine.events.clear()

                        now = time.monotonic()
                        if now - last_export >= 5:
                            date = datetime.now(timezone.utc).date().isoformat()
                            if date != self.daily_date:
                                self.daily_date = date
                                self.notifier.notify(
                                    dict(
                                        kind="DAILY",
                                        date=date,
                                        equity=str(self.engine.get_equity(self.get_live_prices())),
                                    )
                                )
                            try:
                                self.poll_cycle()
                            except Exception as e:
                                logger.error(f"PaperTraderV3: Error during poll_cycle: {e}", exc_info=True)
                            last_export = now

                    iterations += 1
            finally:
                self.stream.close()
                self.save_persistent_state()

    def _export_status(
        self, snapshot: PortfolioSnapshot, current_prices: Dict[str, Decimal], server_ts: int
    ) -> None:
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
                vol_sma = float(last_r["volume_sma"]) if "volume_sma" in last_r else 1.0
                vol_r = float(last_r["volume"] / vol_sma) if vol_sma > 0 else 1.0

                dist_up_pct = max(0.0, (up - curr_p) / curr_p * 100.0)
                dist_low_pct = max(0.0, (curr_p - low) / curr_p * 100.0)

                cd_l = self.engine.cooldown_tracker.get(f"{sym}_1", 0)
                cd_s = self.engine.cooldown_tracker.get(f"{sym}_-1", 0)
                is_cooldown = (cd_l > self.engine.bar_counter) or (cd_s > self.engine.bar_counter)

                radar[sym] = {
                    "current_price": curr_p,
                    "upper_channel": up,
                    "lower_channel": low,
                    "dist_to_long_pct": round(dist_up_pct, 2),
                    "dist_to_short_pct": round(dist_low_pct, 2),
                    "atr": round(atr, 2),
                    "volume_ratio": round(vol_r, 2),
                    "trend": "BULL (EMA50 > EMA200)" if ema_f > ema_s else "BEAR (EMA50 < EMA200)",
                    "is_in_position": sym in snapshot.open_positions,
                    "is_cooldown": is_cooldown,
                }

        # Next candle countdown
        bar_sec = int(self.bar_interval_ms / 1000)
        next_candle_sec = bar_sec - (int(server_ts / 1000) % bar_sec)

        status_data = {
            "version": "3.0",
            "timestamp": server_ts,
            "mode": "PAPER_STREAM_V3_DYNAMIC_UNIVERSE",
            "stream_ready": self.ready,
            "fresh_symbols": len(current_prices),
            "monitored_symbols_count": len(self.symbols),
            "trading_halted": self.engine.trading_halted,
            "btc_trend_status": self.btc_trend_status,
            "datetime_utc": str(pd.to_datetime(server_ts, unit="ms", utc=True)),
            "next_candle_seconds": next_candle_sec,
            "capital": float(snapshot.equity),
            "peak_equity": float(snapshot.peak_equity),
            "drawdown_pct": float(snapshot.max_drawdown_pct),
            "is_kill_switch_active": snapshot.is_kill_switch_active,
            "total_net_pnl": float(snapshot.total_net_pnl),
            "win_rate_pct": snapshot.win_rate_pct,
            "profit_factor": snapshot.profit_factor,
            "open_positions_count": len(snapshot.open_positions),
            "radar": radar,
            "open_positions": {
                sym: {
                    "direction": "LONG" if p.direction == 1 else "SHORT",
                    "qty": float(p.current_qty),
                    "entry_price": float(p.entry_price),
                    "current_price": float(current_prices.get(sym, p.entry_price)),
                    "current_stop": float(p.current_stop),
                    "is_pyramided": p.is_pyramided,
                    "is_breakeven_set": p.is_breakeven_set,
                    "unrealized_pnl": float(
                        p.current_qty * (current_prices.get(sym, p.entry_price) - p.entry_price)
                        if p.direction == 1
                        else p.current_qty * (p.entry_price - current_prices.get(sym, p.entry_price))
                    ),
                }
                for sym, p in snapshot.open_positions.items()
            },
            "news_defense_active": bool(getattr(self, "news_core", None)),
            "news_profile": "defensive_only" if getattr(self, "news_core", None) else "disabled",
            "news_recent_events": list(getattr(self, "recent_news_events", [])),
            "dynamic_universe_active": ENABLE_DYNAMIC_UNIVERSE,
            "screener_last_run_utc": str(pd.to_datetime(self.last_screener_run_ts * 1000, unit="ms", utc=True)) if self.last_screener_run_ts > 0 else "Just now",
            "screener_next_run_seconds": max(0, int(SCREENER_INTERVAL_HOURS * 3600 - (time.time() - self.last_screener_run_ts))) if self.last_screener_run_ts > 0 else 0,
            "screened_candidates": [c.to_dict() for c in getattr(self, "screened_universe", [])[:30]],
            "closed_trades_count": len(snapshot.closed_trades),
            "gearbox_gear": self.engine.risk_manager.current_gear,
            "gearbox_risk_pct": float(self.engine.risk_manager.get_effective_base_risk_pct() * 100),
            "gearbox_pyramiding_allowed": self.engine.risk_manager.is_pyramiding_allowed,
        }

        self.last_status_cache = status_data

        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        try:
            atomic_json(LIVE_STATUS_FILE, status_data)
        except Exception as e:
            logger.warning(f"PaperTraderV3: Failed to write {LIVE_STATUS_FILE}: {e}")

        # Markdown status file
        news_shield_str = "🛡️ **АКТИВЕН** (Defensive Only: Eject, Squeeze, Runner, Regime Guard)" if getattr(self, "news_core", None) else "⚪ Отключен"
        md = f"""# Bybit Multi-Alpha v3.0 Live Monitor (Port 8082)

**Архитектура**: Dynamic Universe Selection + 4h Donchian Breakout + News Defense Shield  
**Последнее обновление**: {status_data['datetime_utc']}  
**Текущий капитал**: **${snapshot.equity:.2f} USDT** (Старт: ${self.initial_capital} USDT)  
**Общий чистый PnL**: **${snapshot.total_net_pnl:+.2f}**  
**Максимальная просадка**: **{snapshot.max_drawdown_pct:.2f}%**  
**Мониторинг монет**: **{len(self.symbols)} активов** (Top Bybit Linear Perpetuals)  
**Статус тренда BTC**: **{self.btc_trend_status}**  
**Режимный Kill-Switch**: **{'🔴 АКТИВЕН (Риск 0.6%)' if snapshot.is_kill_switch_active else '🟢 В норме (Риск 2.0%)'}**  
**Новостной защитный щит**: {news_shield_str}  
**Открытых позиций**: **{len(snapshot.open_positions)}**  

---

### Открытые позиции (v3.0)

"""
        if snapshot.open_positions:
            md += "| Монета | Направление | Объём | Вход ($) | Текущая ($) | Стоп ($) | Unr. PnL ($) | Пирамидинг |\n"
            md += "|---|---|---|---|---|---|---|---|\n"
            for sym, p in snapshot.open_positions.items():
                curr = current_prices.get(sym, p.entry_price)
                unr = (
                    p.current_qty * (curr - p.entry_price)
                    if p.direction == 1
                    else p.current_qty * (p.entry_price - curr)
                )
                pyr_str = "✅ Доливка +50%" if p.is_pyramided else "Ожидание +2 ATR"
                md += f"| {sym} | {'LONG' if p.direction == 1 else 'SHORT'} | {p.current_qty} | ${p.entry_price:.2f} | ${curr:.2f} | ${p.current_stop:.2f} | ${unr:+.2f} | {pyr_str} |\n"
        else:
            md += "*Нет открытых позиций. Сканер активен на базе Top Bybit Universe с фильтром тренда BTC и кулдауном 3 бара.*\n"

        try:
            LIVE_MD_FILE.write_text(md, encoding="utf-8")
        except Exception as e:
            logger.warning(f"PaperTraderV3: Failed to write {LIVE_MD_FILE}: {e}")

        try:
            self.save_persistent_state()
        except Exception as e:
            logger.warning(f"PaperTraderV3: Failed to save persistent state: {e}")
