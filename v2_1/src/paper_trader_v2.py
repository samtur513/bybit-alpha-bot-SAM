"""
Single-writer Paper Trading Daemon v2.1 on Public Bybit WebSocket.
Enhanced with:
1. Pure WebSocket event stream (no REST polling load, no rate limits)
2. Bybit MNT fee (0.09% / 0.0009)
3. BTC Delta Trend Filter
4. 3-bar Re-entry Cooldown
5. Atomic, crash-resistant state snapshots (BOT_STATE_PERSISTENT_V21.json)
6. Optional Telegram alerts
"""

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
from typing import Dict, List, Optional, Any
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASE_DIR = Path(__file__).resolve().parent.parent
for p in [str(REPO_ROOT), str(BASE_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from v2_1.src.bybit_client import BybitClient
from v2_1.src.config import (
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
    ENABLE_RANGE_SUBMODULE,
    RANGE_RISK_PER_TRADE_PCT,
    RANGE_MAX_POSITIONS,
    RANGE_SL_ATR_MULT,
    RANGE_MIN_TP_PCT,
    RANGE_MAX_TP_PCT,
    ENABLE_REGIME_CLASSIFIER,
    ENABLE_NEWS_DEFENSE,
    ENABLE_MACRO_BLACKOUT,
    ENABLE_DEPEG_GUARD,
    ENABLE_EMERGENCY_EJECT,
    ENABLE_SQUEEZE_ESCAPE,
    ENABLE_RUNNER_PROTECTION,
)
from v2_1.src.core_v2 import CoreV2Engine, PortfolioSnapshot, ActivePosition
from v2_1.src.market_stream import MarketStream
from v2_1.src.news_core import (
    NewsCore,
    NewsConfig,
    PositionView,
    PositionAction,
    Side,
    ActionType,
    EventType,
)
from v2_1.src.persistence import atomic_json
from v2_1.src.range_engine import RangeMeanReversionEngine
from v2_1.src.regime_classifier import MarketRegimeClassifier
from v2_1.src.research_v2_runner import prepare_symbol_indicators
from v2_1.src.telegram_notifier import TelegramNotifier

logger = logging.getLogger("PaperTraderV21")


class PaperTraderV21:
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
        self.symbols = list(symbols or DEFAULT_SYMBOLS)
        self.initial_capital = initial_capital
        self.poll_interval = poll_interval_seconds
        self.client = client or BybitClient(timeout=10, max_retries=3)
        self.bar_interval_ms = bar_interval_ms
        self.lock = threading.RLock()
        self.instrument_rules: Dict[str, Dict[str, Decimal]] = {}
        self._load_instrument_specs()

        self.engine = CoreV2Engine(
            initial_capital=initial_capital,
            instrument_rules=self.instrument_rules,
            base_risk_pct=base_risk_pct,
            max_leverage=max_leverage,
            taker_fee=BASE_TAKER_FEE,
            slippage=BASE_SLIPPAGE,
            portfolio_leverage=PORTFOLIO_LEVERAGE,
            drawdown_limit_pct=DRAWDOWN_LIMIT_PCT,
            min_adx=float(os.getenv("BOT_MIN_ADX", str(MIN_ADX_TREND))),
            partial_tp_ratio=Decimal(os.getenv("BOT_PARTIAL_TP_RATIO", "0")),
            cooldown_bars=REENTRY_COOLDOWN_BARS,
            enable_btc_delta_filter=ENABLE_BTC_DELTA_FILTER,
        )

        self.state_file = PERSISTENT_STATE_FILE
        self.candle_buffers: Dict[str, pd.DataFrame] = {}
        self.last_processed_1h_ts: Dict[str, int] = {s: 0 for s in self.symbols}
        self.prices: Dict[str, Decimal] = {}
        self.price_times: Dict[str, float] = {}
        self.price_server_times: Dict[str, int] = {}
        self.daily_date = ""
        self.batches: Dict[int, Dict[str, Any]] = {}
        self.ready = False
        self.btc_trend_status = "NEUTRAL"

        self._load_persistent_state()
        self.stream = MarketStream(self.symbols, interval=CANDLE_INTERVAL)
        self.notifier = TelegramNotifier()
        self.regime_classifier = MarketRegimeClassifier() if ENABLE_REGIME_CLASSIFIER else None
        self.range_engine = (
            RangeMeanReversionEngine(
                initial_capital=initial_capital,
                instrument_rules=self.instrument_rules,
                risk_per_trade_pct=RANGE_RISK_PER_TRADE_PCT,
                max_concurrent_positions=RANGE_MAX_POSITIONS,
                sl_atr_mult=RANGE_SL_ATR_MULT,
                min_tp_dist_pct=RANGE_MIN_TP_PCT,
                max_tp_dist_pct=RANGE_MAX_TP_PCT,
            )
            if ENABLE_RANGE_SUBMODULE
            else None
        )
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

    def _load_instrument_specs(self) -> None:
        """Fetches Bybit market specifications with full pagination."""
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
                self.last_processed_1h_ts.update(runtime.get("last_processed_1h_ts", {}))
                self.daily_date = runtime.get("daily_date", "")
                logger.info(
                    f"Restored persistent state v2.1: equity=${self.engine.realized_equity:.2f}, "
                    f"open_positions={list(self.engine.open_positions.keys())}"
                )
            except Exception as e:
                logger.error(f"Failed to load persistent snapshot: {e}")
                raise

    def save_persistent_state(self) -> None:
        with self.lock:
            data = self.engine.to_dict()
            data["runtime"] = {
                "last_processed_1h_ts": self.last_processed_1h_ts,
                "daily_date": self.daily_date,
            }
            atomic_json(self.state_file, data)

    def _fetch_history(self) -> Dict[str, pd.DataFrame]:
        buffers = {}
        now = self.client.get_server_time()
        for sym in self.symbols:
            raw = self.client.get_kline(sym, interval=CANDLE_INTERVAL, limit=1000)
            records = [
                dict(
                    timestamp=int(k[0]),
                    **{name: float(k[i]) for i, name in enumerate(("open", "high", "low", "close", "volume", "turnover"), 1)},
                )
                for k in raw
                if int(k[0]) + BAR_INTERVAL_MS <= now
            ]
            df = (
                pd.DataFrame(records)
                .sort_values("timestamp")
                .drop_duplicates("timestamp")
                .reset_index(drop=True)
            )
            if len(df) < 50:
                raise ValueError(f"Insufficient history for {sym}: only {len(df)} bars")
            buffers[sym] = prepare_symbol_indicators(df, lookback=DONCHIAN_WINDOW)
        return buffers

    def get_live_prices(self) -> Dict[str, Decimal]:
        with self.lock:
            now = time.monotonic()
            return {s: p for s, p in self.prices.items() if now - self.price_times.get(s, 0) < 15}

    def _on_bar(self, sym: str, bar: Dict[str, Any]) -> None:
        bar_interval = getattr(self, "bar_interval_ms", 3600000)
        ts = int(bar["start"])
        previous = self.last_processed_1h_ts.get(sym, 0)
        if ts <= previous:
            return
        if ts != previous + bar_interval:
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
        ).tail(1000)
        df = prepare_symbol_indicators(df, lookback=DONCHIAN_WINDOW)
        self.candle_buffers[sym] = df
        self.last_processed_1h_ts[sym] = ts
        self.batches.setdefault(ts, {})[sym] = df.iloc[-1].to_dict()

    def _signals(self) -> None:
        bar_interval = getattr(self, "bar_interval_ms", 3600000)
        prices = self.get_live_prices()
        now_ms = int(time.time() * 1000)
        for ts, bars in list(self.batches.items()):
            if now_ms - (ts + bar_interval) > 60000:
                del self.batches[ts]
                continue
            if (
                set(bars) == set(self.symbols)
                and set(prices) == set(self.symbols)
                and self.ready
                and all(self.price_server_times.get(s, 0) >= ts + bar_interval for s in self.symbols)
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

    def poll_cycle(self) -> PortfolioSnapshot:
        with self.lock:
            prices = self.get_live_prices()
            snap = self.engine.get_portfolio_snapshot(prices)
            self._export_status(snap, prices, int(time.time() * 1000))
            return snap

    def close_all_positions(self) -> None:
        with self.lock:
            prices = self.get_live_prices()
            if set(self.engine.open_positions) - set(prices):
                raise RuntimeError("Fresh quotes required for manual close")
            for sym in list(self.engine.open_positions):
                self.engine._reduce(
                    sym,
                    self.engine.open_positions[sym].current_qty,
                    prices[sym],
                    int(time.time() * 1000),
                    "MANUAL_CLOSE",
                )
            self.save_persistent_state()

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
            # 1. Classify headline
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

            # 2. Check if we hold a position in target_sym
            if target_sym not in self.engine.open_positions:
                return []

            pos = self.engine.open_positions[target_sym]
            mark_price = self.prices.get(target_sym, pos.entry_price)
            curr_p = float(mark_price)
            entry_p = float(pos.entry_price)
            stop_p = float(pos.current_stop)

            # Compute R multiple
            risk_dist = abs(entry_p - stop_p)
            if risk_dist > 0:
                r_mult = ((curr_p - entry_p) / risk_dist) if pos.direction == 1 else ((entry_p - curr_p) / risk_dist)
            else:
                r_mult = 0.0

            pos_view = PositionView(
                symbol=target_sym,
                side=Side.LONG if pos.direction == 1 else Side.SHORT,
                size=float(pos.current_qty),
                entry_price=entry_p,
                current_price=curr_p,
                stop_loss=stop_p,
                unrealized_pnl_r=r_mult,
            )

            # 3. Evaluate Position Matrix defensive shields
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

            # 4. Execute defensive actions
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
                                self.last_processed_1h_ts[sym] = int(df.timestamp.iloc[-1])
                            self.batches.clear()
                            self.ready = True
                            # Initial BTC trend check
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
                            and time.monotonic() - received < 3
                            and abs(time.time() * 1000 - ts) < 10000
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
                            self.poll_cycle()
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

                # Cooldown status
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

        next_candle_sec = 3600 - (int(server_ts / 1000) % 3600)

        status_data = {
            "version": "2.1",
            "timestamp": server_ts,
            "mode": "PAPER_STREAM_V21",
            "stream_ready": self.ready,
            "fresh_symbols": len(current_prices),
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
            "dual_regime_mode": "ACTIVE" if self.range_engine else "STANDALONE_TREND_4H",
            "range_positions_count": len(self.range_engine.open_positions) if self.range_engine else 0,
            "news_defense_active": bool(getattr(self, "news_core", None)),
            "news_profile": "defensive_only" if getattr(self, "news_core", None) else "disabled",
            "closed_trades_count": len(snapshot.closed_trades),
        }

        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        atomic_json(LIVE_STATUS_FILE, status_data)

        # Markdown status file
        news_shield_str = "🛡️ **АКТИВЕН** (Defensive Only: Eject, Squeeze, Runner, Regime Guard)" if getattr(self, "news_core", None) else "⚪ Отключен"
        md = f"""# Bybit Multi-Alpha v2.1 Live Monitor (Port 8081)

**Последнее обновление**: {status_data['datetime_utc']}  
**Текущий капитал**: **${snapshot.equity:.2f} USDT** (Старт: ${self.initial_capital} USDT)  
**Общий чистый PnL**: **${snapshot.total_net_pnl:+.2f}**  
**Максимальная просадка**: **{snapshot.max_drawdown_pct:.2f}%**  
**Статус тренда BTC**: **{self.btc_trend_status}**  
**Режимный Kill-Switch**: **{'🔴 АКТИВЕН (Риск 0.75%)' if snapshot.is_kill_switch_active else '🟢 В норме (Риск 2.5%)'}**  
**Новостной защитный щит**: {news_shield_str}  
**Открытых позиций**: **{len(snapshot.open_positions)}**  

---

### Открытые позиции (v2.1)

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
            md += "*Нет открытых позиций. Сканер активен с фильтром тренда BTC и кулдауном 3 бара.*\n"

        LIVE_MD_FILE.write_text(md, encoding="utf-8")
        self.save_persistent_state()


PaperTraderV2 = PaperTraderV21

