#!/usr/bin/env python3
"""
v3_2/src/runners/run_native_paper.py
Standalone Operational Runner for v3.2 Native Paper Trading Engine.
Protocol: TORG-BOT — REAL-RUNTIME-WIRING-007

Architecture & Invariants:
1. Real Quantitative Engine: Wraps CoreV3Engine with exact original Donchian Trend logic:
   - 120-bar Donchian channel with shift(1) Zero-Lookahead.
   - Triple trend filter: ADX >= 18.0, EMA50 > EMA200, BTC Delta confirmation.
   - Volume filter: Volume >= 1.2 * Volume SMA20.
   - Risk sizing: Half-Kelly position sizing with max leverage cap 3.5x.
   - Position lifecycle: Entry slippage 0.02%, 0.090% legacy MNT taker fee, 0.0324% maker fee.
   - Trailing stops, Cascade Take Profit (TP1/TP2), Fast Scratch rule.
   - Contract-specific 8-hour perpetual funding accrual.
2. Strict Capital Isolation: Starting capital = 300.000000 USDT.
3. Isolated SQLite Ledger: Uses v32_native_ledger.db with WAL mode. NEVER touches v32_paper_ledger.db or Port 8083 state.
4. Truthful Telemetry: Without confirmed market data, reports NOT_STARTED/NO_DATA with equity = None.
5. Strictly Paper Trading: Zero live or demo trading API orders. Zero private keys required.
"""

import argparse
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v3.src.bybit_client import BybitClient
from v3.src.core_v3 import CoreV3Engine, ActivePosition, ClosedTrade, PortfolioSnapshot
from v3.src.persistence import atomic_json
from v3_2.src.config import DEFAULT_SYMBOLS, DONCHIAN_WINDOW, CANDLE_INTERVAL, BAR_INTERVAL_MS
from v3_2.src.regime_classifier import MarketRegimeClassifier
from v3_2.src.paper_trader_v32 import prepare_dual_regime_indicators

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [NativePaperRunner] %(message)s",
)
logger = logging.getLogger("NativePaperRunner")

DEFAULT_COHORT_ID = "TORG_THREE_SYS_20261115_001"
DEFAULT_T0_UTC = "2026-11-17T00:00:00+00:00"
DEFAULT_DB_NAME = "v32_native_ledger.db"
DEFAULT_STATE_NAME = "v32_native_state.json"
DEFAULT_TELEMETRY_NAME = "telemetry_native.json"


class NativePaperRunner:
    """
    Independent v3.2 Native Paper Trading Daemon running real quantitative logic.
    """

    def __init__(
        self,
        db_path: Path,
        telemetry_path: Path,
        state_path: Optional[Path] = None,
        initial_capital: Decimal = Decimal("300.000000"),
        cohort_id: str = DEFAULT_COHORT_ID,
        t0_utc: str = DEFAULT_T0_UTC,
        symbols: Optional[List[str]] = None,
    ):
        self.db_path = Path(db_path).resolve()
        self.telemetry_path = Path(telemetry_path).resolve()
        self.state_path = Path(state_path).resolve() if state_path else self.db_path.parent / DEFAULT_STATE_NAME
        self.initial_capital = initial_capital
        self.cohort_id = cohort_id
        self.t0_utc = t0_utc
        self.symbols = list(symbols) if symbols else list(DEFAULT_SYMBOLS)
        self._running = False

        # Ensure parent directories exist
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.telemetry_path.parent.mkdir(parents=True, exist_ok=True)

        # 1. Initialize Bybit Public Client (Read-Only)
        self.client = BybitClient(timeout=10, max_retries=5)
        self.instrument_rules: Dict[str, Dict[str, Decimal]] = self._build_default_rules()

        # 2. Initialize Real Quantitative CoreV3 Engine
        self.engine = CoreV3Engine(
            initial_capital=self.initial_capital,
            instrument_rules=self.instrument_rules,
            base_risk_pct=Decimal("0.010"),
            max_leverage=Decimal("3.5"),
            taker_fee=Decimal("0.00090"),    # 0.090% Legacy MNT discount
            slippage=Decimal("0.0002"),      # 0.02% simulated fixed
            portfolio_leverage=Decimal("3.5"),
            drawdown_limit_pct=Decimal("30.0"),
            min_adx=18.0,
            cooldown_bars=3,
            enable_btc_delta_filter=True,
            enable_auto_gearbox=True,
            enable_fast_scratch=True,
            enable_cascade_tp=True,
            bar_interval_ms=BAR_INTERVAL_MS,
        )
        self.engine.scanner.min_volume_ratio = 1.2

        # 3. Indicator Buffers & State Tracking
        self.classifier = MarketRegimeClassifier()
        self.candle_buffers: Dict[str, pd.DataFrame] = {}
        self.last_processed_bar_ts: Dict[str, int] = {s: 0 for s in self.symbols}
        self.latest_prices: Dict[str, Decimal] = {}
        self.latest_quote_times: Dict[str, float] = {}
        self.has_market_data = False
        self.btc_trend_status = "NEUTRAL"

        # 4. Initialize Database & Recover State
        self._init_sqlite_schema()
        self.load_state()

    @property
    def total_fees(self) -> Decimal:
        closed = sum((getattr(t, "fees_paid", Decimal("0")) for t in self.engine.closed_trades), Decimal("0"))
        open_f = sum((p.entry_fees for p in self.engine.open_positions.values()), Decimal("0"))
        return closed + open_f

    @property
    def total_funding(self) -> Decimal:
        closed = sum((getattr(t, "funding_pnl", Decimal("0")) for t in self.engine.closed_trades), Decimal("0"))
        open_f = sum((p.funding_pnl for p in self.engine.open_positions.values()), Decimal("0"))
        return closed + open_f

    @property
    def total_turnover(self) -> Decimal:
        return sum(
            (t.initial_qty * t.entry_price + t.final_qty * t.exit_price for t in self.engine.closed_trades),
            Decimal("0"),
        )

    def _build_default_rules(self) -> Dict[str, Dict[str, Decimal]]:
        rules = {}
        for s in self.symbols:
            rules[s] = {
                "minOrderQty": Decimal("0.001"),
                "qtyStep": Decimal("0.001"),
                "tickSize": Decimal("0.01"),
                "minNotional": Decimal("5.0"),
                "maxOrderQty": Decimal("1000000"),
            }
        return rules

    def _init_sqlite_schema(self) -> None:
        """Initializes isolated SQLite schema for native paper ledger."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS native_journal (
                        journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp_ms INTEGER NOT NULL,
                        iso_time TEXT NOT NULL,
                        cohort_id TEXT NOT NULL,
                        realized_equity REAL NOT NULL,
                        unrealized_pnl REAL NOT NULL,
                        total_equity REAL NOT NULL,
                        cash_balance REAL NOT NULL,
                        total_fees REAL NOT NULL,
                        total_funding REAL NOT NULL,
                        peak_equity REAL NOT NULL,
                        positions_count INTEGER NOT NULL,
                        gear INTEGER DEFAULT 3,
                        notes TEXT
                    );
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS native_trades (
                        trade_id TEXT PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        direction INTEGER NOT NULL,
                        entry_time_ms INTEGER NOT NULL,
                        exit_time_ms INTEGER NOT NULL,
                        entry_price REAL NOT NULL,
                        exit_price REAL NOT NULL,
                        qty REAL NOT NULL,
                        gross_pnl REAL NOT NULL,
                        fees_paid REAL NOT NULL,
                        funding_pnl REAL NOT NULL,
                        net_pnl REAL NOT NULL,
                        exit_reason TEXT NOT NULL
                    );
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS native_dedup (
                        event_key TEXT PRIMARY KEY,
                        processed_at_ms INTEGER NOT NULL
                    );
                """)
        finally:
            conn.close()

    def load_state(self) -> None:
        """Restores state from state_file and synchronizes with SQLite."""
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                self.engine.load_from_dict(data.get("engine", {}))
                self.last_processed_bar_ts = data.get("last_processed_bar_ts", self.last_processed_bar_ts)
                logger.info(f"Restored Native state: Realized Equity={self.engine.realized_equity:.4f}, Positions={len(self.engine.open_positions)}")
            except Exception as e:
                logger.warning(f"Failed to load state from {self.state_path}: {e}")

    def save_state(self) -> None:
        """Atomically saves state snapshot to JSON."""
        state_data = {
            "version": "3.2-NATIVE",
            "saved_at_iso": datetime.now(timezone.utc).isoformat(),
            "cohort_id": self.cohort_id,
            "engine": self.engine.to_dict(),
            "last_processed_bar_ts": self.last_processed_bar_ts,
        }
        atomic_json(self.state_path, state_data)

    def ingest_closed_bar(
        self,
        symbol: str,
        bar: Dict[str, Any],
        current_price: Optional[Decimal] = None,
        funding_rate: Optional[Decimal] = None,
        btc_bullish: bool = True,
        btc_bearish: bool = True,
        fault_injection_point: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Processes a closed bar through the real Donchian quantitative cycle:
        1. Authoritative SQLite Dedup check (Read-Only).
        2. Indicator computation with zero-lookahead shift(1).
        3. Signal generation & Half-Kelly position sizing.
        4. Position entry / stop / trailing / cascade TP / fast scratch management.
        5. Single atomic commit to native_journal, native_trades, AND native_dedup (Section P2).
        """
        ts = int(bar["timestamp"])
        event_key = f"{symbol}_{ts}"

        # 1. Authoritative SQLite Dedup Check (Read-Only)
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM native_dedup WHERE event_key = ?;", (event_key,))
            if cursor.fetchone() is not None:
                return {"status": "SKIPPED_DUPLICATE", "symbol": symbol, "ts": ts}
        finally:
            conn.close()

        # Update candle buffer
        record = {
            "timestamp": ts,
            "open": float(bar["open"]),
            "high": float(bar["high"]),
            "low": float(bar["low"]),
            "close": float(bar["close"]),
            "volume": float(bar.get("volume", 1500.0)),
            "turnover": float(bar.get("turnover", float(bar["close"]) * 1500.0)),
        }
        prev_df = self.candle_buffers.get(symbol)
        if prev_df is None or len(prev_df) == 0:
            df = pd.DataFrame([record])
        else:
            df = pd.concat([prev_df, pd.DataFrame([record])], ignore_index=True).tail(500)

        # Compute indicators strictly with shift(1)
        if len(df) >= min(DONCHIAN_WINDOW, 50):
            df = prepare_dual_regime_indicators(df, self.classifier, lookback=DONCHIAN_WINDOW)
        self.candle_buffers[symbol] = df
        self.last_processed_bar_ts[symbol] = ts

        # Close price and quotes
        price = current_price or Decimal(str(bar["close"]))
        self.latest_prices[symbol] = price
        self.latest_quote_times[symbol] = time.monotonic()
        self.has_market_data = True

        # Process Funding Payment if provided
        if funding_rate is not None and funding_rate != Decimal("0"):
            self.engine.process_funding_event(symbol, funding_rate, ts, price)

        # Step quantitative engine
        bar_dict = df.iloc[-1].to_dict() if len(df) > 0 else record
        price_map = {symbol: price}
        prev_trades_count = len(self.engine.closed_trades)

        self.engine.process_bar_event(
            timestamp=ts,
            bars={symbol: bar_dict},
            next_open_prices=price_map,
            manage_positions=True,
            btc_bullish=btc_bullish,
            btc_bearish=btc_bearish,
        )

        new_closed_trades = self.engine.closed_trades[prev_trades_count:]

        # Fault Injection Point (Before Commit) - Section P2 verification
        if fault_injection_point == "before_commit":
            raise RuntimeError("FAULT_INJECTION: Crash immediately before commit in NativePaperRunner")

        # 2. Single Atomic Transaction: Trades + Journal + Dedup Key
        conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        try:
            with conn:  # BEGIN ... COMMIT
                cursor = conn.cursor()
                for trade in new_closed_trades:
                    gross = getattr(trade, "gross_pnl", float((trade.exit_price - trade.entry_price) * trade.final_qty * Decimal(str(trade.direction))))
                    fees = getattr(trade, "fees_paid", 0.0)
                    funding = getattr(trade, "funding_pnl", 0.0)
                    cursor.execute("""
                        INSERT OR REPLACE INTO native_trades (
                            trade_id, symbol, direction, entry_time_ms, exit_time_ms,
                            entry_price, exit_price, qty, gross_pnl, fees_paid,
                            funding_pnl, net_pnl, exit_reason
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (
                        f"T_{trade.symbol}_{trade.entry_time}",
                        trade.symbol,
                        trade.direction,
                        trade.entry_time,
                        trade.exit_time,
                        float(trade.entry_price),
                        float(trade.exit_price),
                        float(trade.initial_qty),
                        float(gross),
                        float(fees),
                        float(funding),
                        float(trade.net_pnl),
                        trade.exit_reason,
                    ))

                # Checkpoint journal
                now_dt = datetime.now(timezone.utc)
                snap = self.engine.get_portfolio_snapshot(self.latest_prices)
                unrealized = float(snap.equity - self.engine.realized_equity)
                cursor.execute("""
                    INSERT INTO native_journal (
                        timestamp_ms, iso_time, cohort_id, realized_equity,
                        unrealized_pnl, total_equity, cash_balance, total_fees,
                        total_funding, peak_equity, positions_count, gear, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    int(now_dt.timestamp() * 1000),
                    now_dt.isoformat(),
                    self.cohort_id,
                    float(self.engine.realized_equity),
                    unrealized,
                    float(snap.equity),
                    float(self.engine.realized_equity),
                    float(self.total_fees),
                    float(self.total_funding),
                    float(self.engine.peak_equity),
                    len(self.engine.open_positions),
                    3,
                    f"closed_bar_{symbol}",
                ))

                # Atomically commit dedup key inside the SAME transaction!
                cursor.execute("""
                    INSERT INTO native_dedup (event_key, processed_at_ms)
                    VALUES (?, ?);
                """, (event_key, int(time.time() * 1000)))
        finally:
            conn.close()

        self.save_state()
        self.export_telemetry()

        return {
            "status": "PROCESSED",
            "symbol": symbol,
            "ts": ts,
            "positions_count": len(self.engine.open_positions),
            "realized_equity": float(self.engine.realized_equity),
        }

    def update_quotes(
        self,
        quotes: Dict[str, Decimal],
        timestamp_ms: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Updates live quotes for continuous MTM equity tracking and accompanies open positions.
        """
        now_mono = time.monotonic()
        ts = timestamp_ms or int(time.time() * 1000)
        for sym, price in quotes.items():
            self.latest_prices[sym] = price
            self.latest_quote_times[sym] = now_mono

        self.has_market_data = True

        # Accompany open positions on tick quotes: manage stops & MTM
        if self.engine.open_positions:
            prev_closed = len(self.engine.closed_trades)
            self.engine.process_price_event(timestamp=ts, prices=self.latest_prices)
            new_closed = self.engine.closed_trades[prev_closed:]
            if new_closed:
                conn = sqlite3.connect(str(self.db_path), timeout=30.0)
                try:
                    with conn:
                        cursor = conn.cursor()
                        for trade in new_closed:
                            gross = getattr(trade, "gross_pnl", float((trade.exit_price - trade.entry_price) * trade.final_qty * Decimal(str(trade.direction))))
                            fees = getattr(trade, "fees_paid", 0.0)
                            funding = getattr(trade, "funding_pnl", 0.0)
                            cursor.execute("""
                                INSERT OR REPLACE INTO native_trades (
                                    trade_id, symbol, direction, entry_time_ms, exit_time_ms,
                                    entry_price, exit_price, qty, gross_pnl, fees_paid,
                                    funding_pnl, net_pnl, exit_reason
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                            """, (
                                f"T_{trade.symbol}_{trade.entry_time}",
                                trade.symbol,
                                trade.direction,
                                trade.entry_time,
                                trade.exit_time,
                                float(trade.entry_price),
                                float(trade.exit_price),
                                float(trade.initial_qty),
                                float(gross),
                                float(fees),
                                float(funding),
                                float(trade.net_pnl),
                                trade.exit_reason,
                            ))
                finally:
                    conn.close()

        self.save_state()
        return self.export_telemetry()

    def warm_up_history_from_bybit(self) -> None:
        """Loads historical closed klines from Bybit public REST API for all universe symbols."""
        logger.info(f"Warming up historical kline buffers via Bybit public REST API for {len(self.symbols)} symbols...")
        try:
            now_srv = self.client.get_server_time()
            for sym in self.symbols:  # Full original universe, not sliced!
                try:
                    raw = self.client.get_kline(sym, interval=CANDLE_INTERVAL, limit=150)
                    if not raw:
                        continue
                    records = [
                        dict(
                            timestamp=int(k[0]),
                            open=float(k[1]), high=float(k[2]), low=float(k[3]),
                            close=float(k[4]), volume=float(k[5]), turnover=float(k[6])
                        )
                        for k in raw if int(k[0]) + BAR_INTERVAL_MS <= now_srv
                    ]
                    df = pd.DataFrame(records).sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
                    if len(df) >= min(DONCHIAN_WINDOW, 50):
                        df = prepare_dual_regime_indicators(df, self.classifier, lookback=DONCHIAN_WINDOW)
                        self.candle_buffers[sym] = df
                        self.latest_prices[sym] = Decimal(str(df["close"].iloc[-1]))
                        self.latest_quote_times[sym] = time.monotonic()
                        self.last_processed_bar_ts[sym] = int(df["timestamp"].iloc[-1])
                except Exception as sym_err:
                    logger.debug(f"Warm-up skip for {sym}: {sym_err}")
            self.has_market_data = len(self.candle_buffers) > 0
            logger.info(f"Warm-up complete. Initialized buffers for {len(self.candle_buffers)} symbols.")
        except Exception as e:
            logger.warning(f"Public market data warm-up failed (continuing in offline/safe mode): {e}")

    def poll_market_step(self) -> Dict[str, Any]:
        """
        Executes one continuous forward market data polling cycle:
        1. Queries Bybit server time: now_srv = client.get_server_time().
        2. Queries live tickers via Bybit public REST:
           - Updates latest_prices and latest_quote_times for all active symbols.
           - Updates MTM equity and accompanies open positions on live quotes.
        3. Queries latest 1h klines for each symbol in self.symbols:
           - Validates candle close timestamp: strictly requires kline_open_ts + 3600_000 <= now_srv.
             Never processes the currently in-progress unclosed bar.
           - Checks if kline_open_ts > last_processed_bar_ts[symbol].
           - If a new closed bar is detected:
             Calls ingest_closed_bar() in chronological sequence.
        4. Detects funding events and applies contract-specific funding payments.
        5. Handles network latency, transient errors, rate limits with backoff without crashing.
        6. Atomically updates state and exports standardized telemetry.
        """
        try:
            now_srv = self.client.get_server_time()
            tickers = self.client.get_tickers_detail(category="linear")
            ticker_map = {t["symbol"]: t for t in tickers if "symbol" in t}

            # 1. Update live quotes for all symbols in universe
            quotes_to_update = {}
            funding_map = {}
            for sym in self.symbols:
                t = ticker_map.get(sym)
                if t and t.get("lastPrice"):
                    try:
                        p = Decimal(str(t["lastPrice"]))
                        quotes_to_update[sym] = p
                        if t.get("fundingRate"):
                            funding_map[sym] = Decimal(str(t["fundingRate"]))
                    except Exception:
                        pass

            if quotes_to_update:
                self.update_quotes(quotes_to_update, timestamp_ms=now_srv)

            # 2. Check BTC Trend Filter from BTC candle buffer
            btc_bullish = True
            btc_bearish = True
            btc_df = self.candle_buffers.get("BTCUSDT")
            if btc_df is not None and len(btc_df) >= 50:
                btc_close = btc_df["close"].iloc[-1]
                btc_ema50 = btc_df["ema_fast"].iloc[-1] if "ema_fast" in btc_df else btc_close
                btc_bullish = bool(btc_close >= btc_ema50)
                btc_bearish = bool(btc_close <= btc_ema50)

            # 3. Poll for newly closed 1h bars
            processed_count = 0
            for sym in self.symbols:
                try:
                    raw = self.client.get_kline(sym, interval=CANDLE_INTERVAL, limit=3)
                    if not raw:
                        continue
                    # Filter only strictly closed bars: open_ts + 3600000 <= now_srv
                    closed_bars = [k for k in raw if int(k[0]) + BAR_INTERVAL_MS <= now_srv]
                    # Sort ascending (oldest first)
                    closed_bars.sort(key=lambda k: int(k[0]))
                    for k in closed_bars:
                        bar_ts = int(k[0])
                        if bar_ts > self.last_processed_bar_ts.get(sym, 0):
                            bar_dict = {
                                "timestamp": bar_ts,
                                "open": float(k[1]),
                                "high": float(k[2]),
                                "low": float(k[3]),
                                "close": float(k[4]),
                                "volume": float(k[5]),
                                "turnover": float(k[6]),
                            }
                            f_rate = funding_map.get(sym)
                            res = self.ingest_closed_bar(
                                symbol=sym,
                                bar=bar_dict,
                                current_price=quotes_to_update.get(sym),
                                funding_rate=f_rate,
                                btc_bullish=btc_bullish,
                                btc_bearish=btc_bearish,
                            )
                            if res.get("status") == "PROCESSED":
                                processed_count += 1
                except Exception as sym_err:
                    logger.debug(f"Error checking klines for {sym}: {sym_err}")

            return {
                "status": "SUCCESS",
                "processed_bars": processed_count,
                "server_time_ms": now_srv,
            }
        except Exception as e:
            logger.warning(f"poll_market_step failed: {e}")
            return {"status": "ERROR", "reason": str(e)}

    def export_telemetry(self) -> Dict[str, Any]:
        """
        Builds and atomically writes standardized telemetry JSON.
        Strict Truthfulness Rule:
        If no market data has arrived or quotes are stale, reports None equity.
        """
        now_dt = datetime.now(timezone.utc)
        now_mono = time.monotonic()

        # Check freshness of quotes
        is_fresh = False
        if self.has_market_data and self.latest_quote_times:
            newest_quote_age = min(now_mono - t for t in self.latest_quote_times.values())
            is_fresh = (0.0 <= newest_quote_age <= 120.0)

        init_cap = float(self.initial_capital)

        if not self.has_market_data or not is_fresh:
            status = "NOT_STARTED" if not self.has_market_data else "STALE"
            val_status = "NOT_STARTED" if not self.has_market_data else "UNVALUED_OR_STALE"
            telemetry = {
                "name": "v3.2 Native",
                "role": "Original Donchian Trend Logic (New Isolated Paper Cohort)",
                "market_type": "BYBIT_LINEAR_PERPETUAL",
                "cohort_id": self.cohort_id,
                "t0_utc": self.t0_utc,
                "status": status,
                "initial_capital_usdt": init_cap,
                "equity_usdt": None,
                "net_pnl_usdt": None,
                "net_return_pct": None,
                "mdd_pct": None,
                "cash_usdt": None,
                "exposure_pct": None,
                "turnover_usdt": float(self.total_turnover),
                "fees_paid_usdt": float(self.total_fees),
                "slippage_cost_usdt": 0.0,
                "funding_paid_usdt": float(self.total_funding),
                "positions_count": len(self.engine.open_positions),
                "positions_detail": {},
                "data_freshness_status": status,
                "valuation_status": val_status,
                "error_count": 0,
                "last_update_iso": now_dt.isoformat(),
                "notes": f"Native Quantitative Cycle: {status} (Awaiting verified market data)",
                "is_active": is_fresh,
                "schedule_active": False,  # Protocol 007 Invariant: T0 is candidate only!
            }
        else:
            snap = self.engine.get_portfolio_snapshot(self.latest_prices)
            eq = float(snap.equity)
            pnl = eq - init_cap
            ret_pct = (pnl / init_cap) * 100.0 if init_cap > 0 else 0.0
            peak = float(self.engine.peak_equity)
            mdd = round(((eq - peak) / peak) * 100.0, 2) if peak > 0 else 0.0

            pos_detail = {}
            for s, pos in self.engine.open_positions.items():
                pos_detail[s] = {
                    "direction": "LONG" if pos.direction == 1 else "SHORT",
                    "entry_price": float(pos.entry_price),
                    "current_qty": float(pos.current_qty),
                    "current_stop": float(pos.current_stop),
                    "bars_held": pos.bars_held,
                }

            tot_exposure = sum(
                (p.current_qty * self.latest_prices.get(p.symbol, p.entry_price) for p in self.engine.open_positions.values()),
                Decimal("0"),
            )
            exposure_pct = round(float(tot_exposure / Decimal(str(eq)) * Decimal("100.0")), 2) if eq > 0 else 0.0

            telemetry = {
                "name": "v3.2 Native",
                "role": "Original Donchian Trend Logic (New Isolated Paper Cohort)",
                "market_type": "BYBIT_LINEAR_PERPETUAL",
                "cohort_id": self.cohort_id,
                "t0_utc": self.t0_utc,
                "status": "FRESH",
                "initial_capital_usdt": round(init_cap, 4),
                "equity_usdt": round(eq, 4),
                "net_pnl_usdt": round(pnl, 4),
                "net_return_pct": round(ret_pct, 3),
                "mdd_pct": mdd,
                "cash_usdt": round(float(self.engine.realized_equity), 4),
                "exposure_pct": exposure_pct,
                "turnover_usdt": float(self.total_turnover),
                "fees_paid_usdt": round(float(self.total_fees), 4),
                "slippage_cost_usdt": 0.0,
                "funding_paid_usdt": round(float(self.total_funding), 4),
                "positions_count": len(self.engine.open_positions),
                "positions_detail": pos_detail,
                "data_freshness_status": "FRESH",
                "valuation_status": "FRESH",
                "error_count": 0,
                "last_update_iso": now_dt.isoformat(),
                "notes": "Linear Perpetuals, Half-Kelly sizing, 0.09% MNT taker fee tier, active engine",
                "is_active": True,
                "schedule_active": False,
            }

        atomic_json(self.telemetry_path, telemetry)
        return telemetry

    def run_preflight_cycle(self) -> Dict[str, Any]:
        """Runs offline/dry-run preflight check and writes initial state."""
        logger.info("Executing Native Paper real-engine preflight check...")
        self.save_state()
        telemetry = self.export_telemetry()
        logger.info(f"Native preflight complete. Status: {telemetry['data_freshness_status']}, Equity: {telemetry['equity_usdt']}")
        return telemetry

    def run(self, duration_sec: Optional[int] = None, interval_sec: int = 5) -> None:
        """Main daemon loop with continuous market data polling and execution."""
        self._running = True
        logger.info(f"Starting Native Paper Runner. DB: {self.db_path}, Telemetry: {self.telemetry_path}")
        self.warm_up_history_from_bybit()
        self.export_telemetry()

        start_time = time.monotonic()
        try:
            while self._running:
                # 1. Step continuous market stream
                self.poll_market_step()

                # 2. Update telemetry
                self.export_telemetry()

                if duration_sec and (time.monotonic() - start_time >= duration_sec):
                    logger.info(f"Target duration of {duration_sec}s reached. Exiting cleanly.")
                    break
                time.sleep(interval_sec)
        except KeyboardInterrupt:
            logger.info("Shutdown requested.")
        finally:
            self._running = False
            self.save_state()
            self.export_telemetry()
            logger.info("Native Paper Runner cleanly stopped.")


def main():
    parser = argparse.ArgumentParser(description="v3.2 Native Paper Trading Runner (Real Engine)")
    parser.add_argument("--dry-run", action="store_true", help="Run preflight validation and exit")
    parser.add_argument("--duration", type=int, default=None, help="Run duration in seconds")
    parser.add_argument("--interval", type=int, default=5, help="Telemetry heartbeat interval in seconds")
    parser.add_argument("--db-path", type=str, default=None, help="Path to isolated SQLite database")
    parser.add_argument("--telemetry-file", type=str, default=None, help="Path to telemetry output JSON")
    parser.add_argument("--cohort-id", type=str, default=DEFAULT_COHORT_ID, help="Canonical cohort ID")
    parser.add_argument("--t0-utc", type=str, default=DEFAULT_T0_UTC, help="Scheduled T0 UTC")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[3]
    db_path = Path(args.db_path) if args.db_path else repo_root / "reports" / DEFAULT_DB_NAME
    telemetry_path = Path(args.telemetry_file) if args.telemetry_file else repo_root / "reports" / DEFAULT_TELEMETRY_NAME

    runner = NativePaperRunner(
        db_path=db_path,
        telemetry_path=telemetry_path,
        initial_capital=Decimal("300.000000"),
        cohort_id=args.cohort_id,
        t0_utc=args.t0_utc,
    )

    if args.dry_run:
        runner.run_preflight_cycle()
        sys.exit(0)

    runner.run(duration_sec=args.duration, interval_sec=args.interval)


if __name__ == "__main__":
    main()
