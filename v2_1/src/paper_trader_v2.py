"""
Paper Trading Daemon v2.1.
Enhanced with Bybit MNT fee (0.09%), BTC Delta Filter, and Re-entry Cooldown.
Maintains atomic persistence to BOT_STATE_PERSISTENT_V21.json.
"""

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import json
import logging
from pathlib import Path
import sys
import time
from typing import Dict, List, Optional, Tuple, Any
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASE_DIR = Path(__file__).resolve().parent.parent
for p in [str(REPO_ROOT), str(BASE_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from v2_1.src.bybit_client import BybitClient
    from v2_1.src.config import (
        REPORTS_DIR, PERSISTENT_STATE_FILE, LIVE_STATUS_FILE, LIVE_MD_FILE,
        BASE_TAKER_FEE, BASE_SLIPPAGE, DEFAULT_SYMBOLS, INITIAL_CAPITAL,
        REENTRY_COOLDOWN_BARS, ENABLE_BTC_DELTA_FILTER
    )
    from v2_1.src.core_v2 import CoreV2Engine, PortfolioSnapshot, ActivePosition, ClosedTrade
except ImportError:
    from src.bybit_client import BybitClient
    from src.config import (
        REPORTS_DIR, PERSISTENT_STATE_FILE, LIVE_STATUS_FILE, LIVE_MD_FILE,
        BASE_TAKER_FEE, BASE_SLIPPAGE, DEFAULT_SYMBOLS, INITIAL_CAPITAL,
        REENTRY_COOLDOWN_BARS, ENABLE_BTC_DELTA_FILTER
    )
    from src.core_v2 import CoreV2Engine, PortfolioSnapshot, ActivePosition, ClosedTrade

logger = logging.getLogger("PaperTraderV21")


class PaperTraderV21:
    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        poll_interval: int = 5,
        initial_capital: Decimal = INITIAL_CAPITAL,
    ):
        self.symbols = symbols or DEFAULT_SYMBOLS
        self.poll_interval = poll_interval
        self.initial_capital = initial_capital
        self.client = BybitClient()

        self.instrument_rules: Dict[str, Dict[str, Decimal]] = {}
        self._load_instrument_specs()

        self.engine = CoreV2Engine(
            initial_capital=self.initial_capital,
            instrument_rules=self.instrument_rules,
            taker_fee=BASE_TAKER_FEE,
            slippage=BASE_SLIPPAGE,
            cooldown_bars=REENTRY_COOLDOWN_BARS,
            enable_btc_delta_filter=ENABLE_BTC_DELTA_FILTER,
        )

        self.state_file = PERSISTENT_STATE_FILE
        self._load_persistent_state()

        self.candle_buffers: Dict[str, pd.DataFrame] = {}
        self.last_processed_1h_ts: Dict[str, int] = {s: 0 for s in self.symbols}

    def _load_persistent_state(self) -> None:
        """Restores saved engine state from disk if available."""
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text(encoding="utf-8"))
                self.engine.load_from_dict(data)
                logger.info(
                    f"RESTORED V2.1 STATE: Realized equity: ${self.engine.realized_equity:.2f}, "
                    f"Open positions: {list(self.engine.open_positions.keys())}, "
                    f"Closed trades: {len(self.engine.closed_trades)}"
                )
            except Exception as e:
                logger.error(f"Failed to load persistent state: {e}")
        else:
            logger.info(f"No existing persistent state found. Initializing fresh v2.1 with ${self.initial_capital} USDT.")

    def save_persistent_state(self) -> None:
        """Persists current engine state to disk atomically."""
        try:
            data = self.engine.to_dict()
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            temp_file = self.state_file.with_suffix(".tmp")
            temp_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
            temp_file.replace(self.state_file)
        except Exception as e:
            logger.error(f"Failed to save persistent state: {e}")

    def _load_instrument_specs(self) -> None:
        """Loads lot size and tick specifications directly from Bybit in bulk."""
        logger.info("Fetching Bybit instruments specifications in bulk...")
        try:
            raw_instruments = self.client.get_instruments_info(category="linear")
            for item in raw_instruments:
                sym = item.get("symbol")
                if sym in self.symbols:
                    ls = item.get("lotSizeFilter", {})
                    self.instrument_rules[sym] = {
                        "minOrderQty": Decimal(str(ls.get("minOrderQty", "0.01"))),
                        "qtyStep": Decimal(str(ls.get("qtyStep", "0.01"))),
                        "minNotional": Decimal(str(ls.get("minNotionalValue", "5.0"))),
                    }
                    logger.info(
                        f"Loaded specs for {sym}: minQty={self.instrument_rules[sym]['minOrderQty']}, "
                        f"step={self.instrument_rules[sym]['qtyStep']}"
                    )
        except Exception as e:
            logger.error(f"Error loading instrument specifications: {e}")

    def _sync_single_symbol(self, sym: str) -> Optional[Tuple[str, pd.DataFrame]]:
        try:
            raw = self.client.get_kline(symbol=sym, interval="60", limit=240)
            if not raw:
                return None
            df = pd.DataFrame(
                raw,
                columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"],
            )
            df["timestamp"] = df["timestamp"].astype(int)
            for col in ["open", "high", "low", "close", "volume", "turnover"]:
                df[col] = df[col].astype(float)

            df = df.sort_values("timestamp").reset_index(drop=True)
            closed_df = df.iloc[:-1].copy().reset_index(drop=True)
            if len(closed_df) < 130:
                return None

            # Donchian 120h
            closed_df["upper"] = closed_df["high"].rolling(120).max()
            closed_df["lower"] = closed_df["low"].rolling(120).min()
            # EMAs
            closed_df["ema_fast"] = closed_df["close"].ewm(span=50, adjust=False).mean()
            closed_df["ema_slow"] = closed_df["close"].ewm(span=200, adjust=False).mean()
            # ATR14
            tr1 = closed_df["high"] - closed_df["low"]
            tr2 = (closed_df["high"] - closed_df["close"].shift(1)).abs()
            tr3 = (closed_df["low"] - closed_df["close"].shift(1)).abs()
            closed_df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
            closed_df["atr"] = closed_df["tr"].rolling(14).mean()
            # Volume SMA20
            closed_df["volume_sma"] = closed_df["volume"].rolling(20).mean()

            return sym, closed_df
        except Exception as e:
            logger.error(f"Failed to fetch historical candles for {sym}: {e}")
            return None

    def sync_candles(self) -> None:
        logger.info(f"Syncing 1h candles for {len(self.symbols)} symbols...")
        with ThreadPoolExecutor(max_workers=8) as executor:
            results = executor.map(self._sync_single_symbol, self.symbols)
            for res in results:
                if res is not None:
                    sym, df = res
                    self.candle_buffers[sym] = df
                    self.last_processed_1h_ts[sym] = int(df.iloc[-1]["timestamp"])

    def get_live_prices(self) -> Dict[str, Decimal]:
        prices = {}
        try:
            tickers = self.client.get_tickers(category="linear")
            for t in tickers:
                sym = t.get("symbol")
                if sym in self.symbols:
                    mp = t.get("markPrice") or t.get("lastPrice")
                    if mp:
                        prices[sym] = Decimal(str(mp))
        except Exception as e:
            logger.error(f"Failed to fetch live tickers: {e}")
        return prices

    def poll_cycle(self) -> PortfolioSnapshot:
        server_ts = int(time.time() * 1000)
        current_prices = self.get_live_prices()

        # 1. Intra-bar monitoring
        for sym, pos in list(self.engine.open_positions.items()):
            mark_p = current_prices.get(sym)
            if mark_p is None:
                continue

            rules = self.instrument_rules.get(sym, {})
            qty_step = rules.get("qtyStep", Decimal("0.01"))
            min_order_qty = rules.get("minOrderQty", Decimal("0.01"))

            high = max(mark_p, pos.highest_price)
            low = min(mark_p, pos.lowest_price)

            action = self.engine.pyramiding_engine.evaluate_bar(
                pos=pos,
                current_high=high,
                current_low=low,
                current_close=mark_p,
                current_atr=pos.initial_atr,
                qty_step=qty_step,
                min_order_qty=min_order_qty,
            )

            if action.action_type == "EXIT":
                logger.warning(f"🚨 STOP LOSS TRIGGERED for {sym} at {mark_p} (Stop: {pos.current_stop})")
                exit_price = (
                    mark_p * (Decimal("1") - BASE_SLIPPAGE)
                    if pos.direction == 1
                    else mark_p * (Decimal("1") + BASE_SLIPPAGE)
                )
                gross = (
                    pos.current_qty * (exit_price - pos.entry_price)
                    if pos.direction == 1
                    else pos.current_qty * (pos.entry_price - exit_price)
                )
                fee = pos.current_qty * exit_price * BASE_TAKER_FEE
                net = gross - fee
                self.engine.realized_equity += net
                self.engine.risk_manager.record_trade_result(net > Decimal("0"))
                self.engine.closed_trades.append(
                    ClosedTrade(
                        symbol=sym,
                        direction=pos.direction,
                        entry_time=pos.entry_time,
                        exit_time=server_ts,
                        entry_price=pos.entry_price,
                        exit_price=exit_price,
                        initial_qty=pos.initial_qty,
                        final_qty=pos.current_qty,
                        net_pnl=net,
                        exit_reason=action.exit_reason or "TRAILED_STOP",
                        was_pyramided=pos.is_pyramided,
                        hold_hours=(server_ts - pos.entry_time) / 3600000.0,
                    )
                )
                key = f"{sym}_{pos.direction}"
                self.engine.cooldown_tracker[key] = self.engine.bar_counter + REENTRY_COOLDOWN_BARS
                del self.engine.open_positions[sym]
                self.save_persistent_state()
                logger.info(f"Position closed. Realized PnL: ${net:+.2f}")

            elif action.action_type == "PYRAMID_ADD":
                logger.info(f"🚀 ANTI-MARTINGALE PYRAMID TRIGGERED for {sym}! Stop locked in breakeven ({pos.current_stop}), adding +{action.added_qty} volume.")
                fee = action.added_qty * mark_p * BASE_TAKER_FEE
                self.engine.realized_equity -= fee
                self.save_persistent_state()

        # 2. Check 1h candle boundary
        new_candle_detected = False
        btc_last_ts = self.last_processed_1h_ts.get("BTCUSDT", 0)
        try:
            raw_k = self.client.get_kline(symbol="BTCUSDT", interval="60", limit=2)
            if len(raw_k) >= 2:
                closed_candle_ts = int(raw_k[1][0])
                if closed_candle_ts > btc_last_ts:
                    new_candle_detected = True
        except Exception as e:
            logger.warning(f"Error checking candle close: {e}")

        if new_candle_detected:
            logger.info("New 1h candle close detected across universe! Re-evaluating signals...")
            self.sync_candles()

            bars_dict = {}
            next_opens = {}
            for sym in self.symbols:
                df = self.candle_buffers.get(sym)
                if df is not None and len(df) > 0:
                    last_row = df.iloc[-1]
                    bars_dict[sym] = {
                        "close": float(last_row["close"]),
                        "high": float(last_row["high"]),
                        "low": float(last_row["low"]),
                        "upper": float(last_row["upper"]),
                        "lower": float(last_row["lower"]),
                        "ema_fast": float(last_row["ema_fast"]),
                        "ema_slow": float(last_row["ema_slow"]),
                        "atr": float(last_row["atr"]),
                        "volume": float(last_row["volume"]),
                        "volume_sma": float(last_row["volume_sma"]),
                    }
                    next_opens[sym] = current_prices.get(sym, Decimal(str(last_row["close"])))

            # Evaluate BTC market trend
            btc_bullish = True
            btc_bearish = True
            if "BTCUSDT" in bars_dict:
                b_c = bars_dict["BTCUSDT"]["close"]
                b_ef = bars_dict["BTCUSDT"]["ema_fast"]
                b_es = bars_dict["BTCUSDT"]["ema_slow"]
                btc_bullish = (b_c > b_ef) and (b_ef > b_es)
                btc_bearish = (b_c < b_ef) and (b_ef < b_es)

            latest_ts = max(self.last_processed_1h_ts.values())
            self.engine.process_bar_event(
                latest_ts,
                bars_dict,
                next_opens,
                btc_bullish=btc_bullish,
                btc_bearish=btc_bearish,
            )
            self.save_persistent_state()

        snapshot = self.engine.get_portfolio_snapshot(current_prices)
        self._export_status(snapshot, current_prices, server_ts)
        return snapshot

    def _export_status(self, snapshot: PortfolioSnapshot, current_prices: Dict[str, Decimal], server_ts: int) -> None:
        radar = {}
        btc_df = self.candle_buffers.get("BTCUSDT")
        btc_trend_str = "BTC Нейтрален"
        if btc_df is not None and len(btc_df) > 0:
            last_btc = btc_df.iloc[-1]
            b_c = float(last_btc["close"])
            b_ef = float(last_btc["ema_fast"])
            b_es = float(last_btc["ema_slow"])
            if b_c > b_ef and b_ef > b_es:
                btc_trend_str = "🟢 BTC БЫЧИЙ (Лонги альтов РАЗРЕШЕНЫ)"
            elif b_c < b_ef and b_ef < b_es:
                btc_trend_str = "🔴 BTC МЕДВЕЖИЙ (Шорты альтов РАЗРЕШЕНЫ)"
            else:
                btc_trend_str = "🟡 BTC НЕОПРЕДЕЛЕН (Входы в альты заблокированы)"

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
                in_cooldown = False
                for k, exp in self.engine.cooldown_tracker.items():
                    if k.startswith(sym) and self.engine.bar_counter < exp:
                        in_cooldown = True
                        break

                radar[sym] = {
                    "current_price": curr_p,
                    "upper_channel": up,
                    "lower_channel": low,
                    "dist_to_long_pct": round(dist_up_pct, 2),
                    "dist_to_short_pct": round(dist_low_pct, 2),
                    "atr": round(atr, 4),
                    "volume_ratio": round(vol_r, 2),
                    "trend": "BULL (EMA50 > EMA200)" if ema_f > ema_s else "BEAR (EMA50 < EMA200)",
                    "is_in_position": sym in snapshot.open_positions,
                    "in_cooldown": in_cooldown,
                }

        next_candle_sec = 3600 - (int(server_ts / 1000) % 3600)

        status_data = {
            "version": "v2.1",
            "features": "BTC Delta Filter + Cooldown 3h + MNT Fee 0.09%",
            "btc_market_filter": btc_trend_str,
            "timestamp": server_ts,
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
            "closed_trades_count": len(snapshot.closed_trades),
        }

        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        LIVE_STATUS_FILE.write_text(json.dumps(status_data, indent=2), encoding="utf-8")
        self.save_persistent_state()

    def run_daemon(self) -> None:
        logger.info(f"Starting Paper Trading daemon v2.1 on {self.symbols}...")
        self.sync_candles()
        while True:
            try:
                self.poll_cycle()
            except Exception as e:
                logger.error(f"Error in poll cycle: {e}")
            time.sleep(self.poll_interval)
