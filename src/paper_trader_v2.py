"""
Paper Trading Engine v2.0 for Bybit V5 Live Market.
Connects directly to official Bybit V5 Linear Perpetuals endpoints.
Executes CoreV2Engine rules in real-time:
- Live 1h candle indicator pipeline
- Intra-candle stop loss & breakeven monitoring
- Real-time Anti-Martingale Pyramiding (+50% at +2x ATR)
- Half-Kelly position sizing with Regime Kill-Switch
- Real-time status reporting to JSON and Markdown
"""

import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.bybit_client import BybitClient
from src.config import REPORTS_DIR, BASE_TAKER_FEE, BASE_SLIPPAGE
from src.core_v2 import CoreV2Engine, PortfolioSnapshot, ActivePosition
from src.research_v2_runner import prepare_symbol_indicators

logger = logging.getLogger("PaperTraderV2")

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT",
    "SUIUSDT", "DOGEUSDT", "AVAXUSDT",
    "NEARUSDT", "LINKUSDT", "APTUSDT",
    "ADAUSDT", "XRPUSDT", "BNBUSDT",
    "TIAUSDT", "INJUSDT", "ARBUSDT",
    "OPUSDT", "1000PEPEUSDT", "RENDERUSDT",
]


class PaperTraderV2:
    """Live paper trading engine running on Bybit V5 market data."""

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        initial_capital: Decimal = Decimal("300.0"),
        base_risk_pct: Decimal = Decimal("0.025"),
        max_leverage: Decimal = Decimal("3.5"),
        poll_interval_seconds: int = 5,
        client: Optional[BybitClient] = None,
    ):
        self.symbols = symbols or list(DEFAULT_SYMBOLS)
        self.initial_capital = initial_capital
        self.poll_interval = poll_interval_seconds
        self.client = client or BybitClient()

        # Dynamic instrument rules fetched from Bybit
        self.instrument_rules: Dict[str, Dict[str, Decimal]] = {}
        self._load_instrument_specs()

        # Core engine
        self.engine = CoreV2Engine(
            initial_capital=self.initial_capital,
            instrument_rules=self.instrument_rules,
            base_risk_pct=base_risk_pct,
            max_leverage=max_leverage,
            taker_fee=BASE_TAKER_FEE,
            slippage=BASE_SLIPPAGE,
        )

        # Candle buffers: symbol -> list of closed 1h bars
        self.candle_buffers: Dict[str, pd.DataFrame] = {}
        self.last_processed_1h_ts: Dict[str, int] = {s: 0 for s in self.symbols}
        self.last_funding_ts: Dict[str, int] = {s: 0 for s in self.symbols}

    def _load_instrument_specs(self) -> None:
        """Loads lot size and tick specifications directly from Bybit in bulk."""
        logger.info("Fetching Bybit instruments specifications in bulk...")
        try:
            all_specs = self.client.get_instruments_info(category="linear")
            spec_map = {item.get("symbol"): item for item in all_specs if "symbol" in item}
            for sym in self.symbols:
                spec = spec_map.get(sym)
                if spec:
                    lot_filter = spec.get("lotSizeFilter", {})
                    price_filter = spec.get("priceFilter", {})
                    self.instrument_rules[sym] = {
                        "minOrderQty": Decimal(str(lot_filter.get("minOrderQty", "0.01"))),
                        "qtyStep": Decimal(str(lot_filter.get("qtyStep", "0.01"))),
                        "minNotional": Decimal(str(lot_filter.get("minNotionalValue", "5.0"))),
                        "tickSize": Decimal(str(price_filter.get("tickSize", "0.01"))),
                    }
                    logger.info(f"Loaded specs for {sym}: minQty={self.instrument_rules[sym]['minOrderQty']}, step={self.instrument_rules[sym]['qtyStep']}")
                else:
                    self.instrument_rules[sym] = {
                        "minOrderQty": Decimal("0.001" if "BTC" in sym else "0.01"),
                        "qtyStep": Decimal("0.001" if "BTC" in sym else "0.01"),
                        "minNotional": Decimal("5.0"),
                        "tickSize": Decimal("0.10" if "BTC" in sym else "0.01"),
                    }
        except Exception as e:
            logger.warning(f"Error fetching bulk instrument specs: {e}")
            for sym in self.symbols:
                self.instrument_rules[sym] = {
                    "minOrderQty": Decimal("0.001" if "BTC" in sym else "0.01"),
                    "qtyStep": Decimal("0.001" if "BTC" in sym else "0.01"),
                    "minNotional": Decimal("5.0"),
                    "tickSize": Decimal("0.10" if "BTC" in sym else "0.01"),
                }

    def sync_candles(self) -> None:
        """Fetches 150 closed 1-hour candles for each symbol in parallel to warm up indicators."""
        logger.info(f"Syncing 1h historical candles for {len(self.symbols)} symbols from Bybit in parallel...")

        def _fetch_one(sym: str):
            try:
                raw_klines = self.client.get_kline(symbol=sym, interval="60", limit=160)
                if not raw_klines:
                    return sym, None
                records = []
                for k in raw_klines:
                    records.append({
                        "timestamp": int(k[0]),
                        "open": float(k[1]),
                        "high": float(k[2]),
                        "low": float(k[3]),
                        "close": float(k[4]),
                        "volume": float(k[5]),
                        "turnover": float(k[6]),
                    })
                df = pd.DataFrame(records).sort_values("timestamp").reset_index(drop=True)
                closed_df = df.iloc[:-1].copy()
                prep_df = prepare_symbol_indicators(closed_df, lookback=120)
                return sym, prep_df
            except Exception as e:
                logger.error(f"Error syncing candles for {sym}: {e}")
                return sym, None

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(_fetch_one, self.symbols))

        synced_count = 0
        for sym, prep_df in results:
            if prep_df is not None and len(prep_df) > 0:
                self.candle_buffers[sym] = prep_df
                self.last_processed_1h_ts[sym] = int(prep_df["timestamp"].iloc[-1])
                synced_count += 1

        logger.info(f"Candle sync complete: {synced_count}/{len(self.symbols)} symbols ready.")

    def get_live_prices(self) -> Dict[str, Decimal]:
        """Fetches latest live ticker prices for all symbols in a single bulk call."""
        try:
            all_tickers = self.client.get_linear_tickers()
            prices = {}
            for sym in self.symbols:
                if sym in all_tickers:
                    prices[sym] = all_tickers[sym]
            return prices
        except Exception as e:
            logger.warning(f"Failed to fetch bulk ticker prices: {e}")
            return {}

    def poll_cycle(self) -> PortfolioSnapshot:
        """
        Executes a single monitoring cycle:
        1. Checks current prices for intra-bar stop hit and pyramiding triggers.
        2. Checks if a new 1h candle has closed and evaluates breakout entries.
        3. Exports real-time status report.
        """
        current_prices = self.get_live_prices()
        if not current_prices:
            logger.warning("No price updates received this cycle.")
            return self.engine.get_portfolio_snapshot({})

        server_ts = self.client.get_server_time()

        # 1. Intra-bar stop check & Pyramiding trigger
        for sym, pos in list(self.engine.open_positions.items()):
            mark_p = current_prices.get(sym)
            if mark_p is None:
                continue

            rules = self.instrument_rules.get(sym, {})
            qty_step = rules.get("qtyStep", Decimal("0.01"))
            min_order_qty = rules.get("minOrderQty", Decimal("0.01"))

            # Approximate current bar high/low from mark price
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
                gross = pos.current_qty * (exit_price - pos.entry_price) if pos.direction == 1 else pos.current_qty * (pos.entry_price - exit_price)
                fee = pos.current_qty * exit_price * BASE_TAKER_FEE
                net = gross - fee
                self.engine.realized_equity += net
                self.engine.risk_manager.record_trade_result(net > Decimal("0"))
                del self.engine.open_positions[sym]
                logger.info(f"Position closed. Realized PnL: ${net:+.2f}")

            elif action.action_type == "PYRAMID_ADD":
                logger.info(f"🚀 ANTI-MARTINGALE PYRAMID TRIGGERED for {sym}! Stop locked in breakeven ({pos.current_stop}), adding +{action.added_qty} volume.")
                fee = action.added_qty * mark_p * BASE_TAKER_FEE
                self.engine.realized_equity -= fee

        # 2. Check for closed 1h candle (monitors BTCUSDT candle boundary)
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

            # Build bars dict for CoreV2Engine
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

            latest_ts = max(self.last_processed_1h_ts.values())
            self.engine.process_bar_event(latest_ts, bars_dict, next_opens)

        snapshot = self.engine.get_portfolio_snapshot(current_prices)
        self._export_status(snapshot, current_prices, server_ts)
        return snapshot

    def open_demo_position(self, symbol: str = "SOLUSDT", direction: int = 1) -> bool:
        """Opens an instant demo paper position for UI visualization and verification."""
        prices = self.get_live_prices()
        p = prices.get(symbol)
        if p is None:
            return False

        rules = self.instrument_rules.get(symbol, {})
        step = rules.get("qtyStep", Decimal("0.1" if "SOL" in symbol else "0.01"))
        min_qty = rules.get("minOrderQty", Decimal("0.1" if "SOL" in symbol else "0.01"))

        # 2.5% risk
        risk_budget = self.engine.realized_equity * Decimal("0.025")
        # Approximate 2.5 ATR stop (or 2.5% price dist)
        stop_dist = p * Decimal("0.025")
        raw_qty = risk_budget / stop_dist
        qty = (raw_qty / step).quantize(Decimal("1")) * step
        if qty < min_qty:
            qty = min_qty

        stop_p = p - stop_dist if direction == 1 else p + stop_dist

        self.engine.open_positions[symbol] = ActivePosition(
            symbol=symbol,
            direction=direction,
            initial_qty=qty,
            current_qty=qty,
            entry_price=p,
            current_stop=stop_p,
            initial_atr=stop_dist / Decimal("2.5"),
            highest_price=p,
            lowest_price=p,
            entry_time=int(time.time() * 1000),
            is_pyramided=False,
            is_breakeven_set=False,
        )
        logger.info(f"Opened DEMO position: {symbol} {'LONG' if direction == 1 else 'SHORT'}, qty={qty}, entry={p}")
        self.poll_cycle()
        return True

    def close_all_positions(self) -> None:
        """Closes all active paper positions immediately at current market price."""
        prices = self.get_live_prices()
        for sym, pos in list(self.engine.open_positions.items()):
            p = prices.get(sym, pos.entry_price)
            gross = pos.current_qty * (p - pos.entry_price) if pos.direction == 1 else pos.current_qty * (pos.entry_price - p)
            fee = pos.current_qty * p * BASE_TAKER_FEE
            net = gross - fee
            self.engine.realized_equity += net
            self.engine.risk_manager.record_trade_result(net > Decimal("0"))
            del self.engine.open_positions[sym]
            logger.info(f"Manually closed {sym}. Net PnL: ${net:+.2f}")
        self.poll_cycle()

    def _export_status(self, snapshot: PortfolioSnapshot, current_prices: Dict[str, Decimal], server_ts: int) -> None:
        """Saves JSON and Markdown live status including scanner radar."""
        # Calculate market radar
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
                }

        next_candle_sec = 3600 - (int(server_ts / 1000) % 3600)

        status_data = {
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
        json_file = REPORTS_DIR / "PAPER_TRADING_LIVE.json"
        json_file.write_text(json.dumps(status_data, indent=2), encoding="utf-8")

        # Markdown formatted report
        md = f"""# Bybit Paper Trading Live Monitor (v2.0)

**Последнее обновление**: {status_data['datetime_utc']}  
**Текущий капитал**: **${snapshot.equity:.2f} USDT** (Старт: ${self.initial_capital} USDT)  
**Общий чистый PnL**: **${snapshot.total_net_pnl:+.2f}**  
**Максимальная просадка**: **{snapshot.max_drawdown_pct:.2f}%**  
**Режимный Kill-Switch**: **{'🔴 АКТИВЕН (Риск 0.75%)' if snapshot.is_kill_switch_active else '🟢 В норме (Риск 2.5%)'}**  
**Открытых позиций**: **{len(snapshot.open_positions)}**  

---

### Открытые позиции

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
            md += "*Нет открытых позиций. Сканер активен и ожидает подтвержденных пробоев каналов.*\n"

        md_file = REPORTS_DIR / "PAPER_TRADING_LIVE.md"
        md_file.write_text(md, encoding="utf-8")

    def run_daemon(self, max_iterations: Optional[int] = None) -> None:
        """Runs the live polling loop indefinitely or for max_iterations."""
        logger.info(f"Starting Paper Trading daemon on {self.symbols} (poll interval: {self.poll_interval}s)...")
        self.sync_candles()
        iterations = 0

        while True:
            try:
                snap = self.poll_cycle()
                iterations += 1
                if iterations % 6 == 0:  # Print heartbeat every minute (at 10s intervals)
                    logger.info(f"[Heartbeat] Equity: ${snap.equity:.2f} | Open: {len(snap.open_positions)} | KillSwitch: {snap.is_kill_switch_active}")
                if max_iterations and iterations >= max_iterations:
                    break
                time.sleep(self.poll_interval)
            except KeyboardInterrupt:
                logger.info("Paper Trading daemon stopped by user.")
                break
            except Exception as e:
                logger.error(f"Error in poll cycle: {e}")
                time.sleep(self.poll_interval)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    trader = PaperTraderV2(symbols=["BTCUSDT", "ETHUSDT", "SOLUSDT"], poll_interval_seconds=5)
    print("Running single test cycle on live Bybit data...")
    snapshot = trader.poll_cycle()
    print(f"\nCycle OK! Equity: ${snapshot.equity:.2f}, Live Bybit connection verified.")
