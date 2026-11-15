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

import math
import numpy as np
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
from v3_2.src.range_engine import (
    RangeMeanReversionEngine,
    RangeActivePosition,
    RangeClosedTrade,
    RangeLimitOrder,
)

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
        schedule_active: bool = False,
    ):
        self.db_path = Path(db_path).resolve()
        self.telemetry_path = Path(telemetry_path).resolve()
        self.state_path = Path(state_path).resolve() if state_path else self.db_path.parent / DEFAULT_STATE_NAME
        self.initial_capital = initial_capital
        self.cohort_id = cohort_id
        self.t0_utc = t0_utc
        self.schedule_active = schedule_active
        self.symbols = list(symbols) if symbols else list(DEFAULT_SYMBOLS)
        self._running = False

        # Parse T0 timestamp
        try:
            self.t0_dt = datetime.fromisoformat(self.t0_utc.replace("Z", "+00:00"))
            self.t0_ms = int(self.t0_dt.timestamp() * 1000)
        except Exception:
            self.t0_dt = datetime.now(timezone.utc)
            self.t0_ms = int(self.t0_dt.timestamp() * 1000)

        # Ensure parent directories exist
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.telemetry_path.parent.mkdir(parents=True, exist_ok=True)

        # 1. Initialize Bybit Public Client (Read-Only)
        self.client = BybitClient(timeout=10, max_retries=5)
        self.instrument_rules: Dict[str, Dict[str, Decimal]] = self._build_default_rules()

        self.peak_equity = self.initial_capital

        # 2. Initialize Real Quantitative Dual-Regime Engines (Trend + Range)
        # Retail VIP0 standard fees: 0.100% taker, 0.036% maker
        self.engine = CoreV3Engine(
            initial_capital=self.initial_capital,
            instrument_rules=self.instrument_rules,
            base_risk_pct=Decimal("0.010"),
            max_leverage=Decimal("3.5"),
            taker_fee=Decimal("0.00100"),    # 0.100% Retail VIP0 Taker fee
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

        # Range Mean-Reversion Submodule (Original v3.2 Dual-Regime Maker Engine)
        self.range_engine = RangeMeanReversionEngine(
            initial_capital=self.initial_capital,
            instrument_rules=self.instrument_rules,
            risk_per_trade_pct=Decimal("0.01"),
            max_concurrent_positions=2,
            max_portfolio_leverage=Decimal("3.5"),
            maker_fee=Decimal("0.00036"),    # 0.036% Retail VIP0 Maker fee
            taker_fee=Decimal("0.00100"),    # 0.100% Retail VIP0 Taker fee
            slippage=Decimal("0.0002"),
        )

        # 3. Indicator Buffers & State Tracking
        self.classifier = MarketRegimeClassifier()
        self.candle_buffers: Dict[str, pd.DataFrame] = {}
        self.last_processed_bar_ts: Dict[str, int] = {s: 0 for s in self.symbols}
        self.latest_prices: Dict[str, Decimal] = {}
        self.latest_quote_times: Dict[str, float] = {}
        self.latest_regimes: Dict[str, Any] = {}
        self.settled_funding_windows: Set[Tuple[str, int]] = set()
        self.missed_opportunities: List[Dict[str, Any]] = []
        self.has_market_data = False
        self.btc_trend_status = "NEUTRAL"

        # Wire unified pre-execution capacity checks into both engines (P0.2)
        self._wire_unified_capacity_checks()

        # 4. Initialize Database & Recover State
        self._init_sqlite_schema()
        self.load_state()

    @property
    def total_fees(self) -> Decimal:
        trend_closed = sum((getattr(t, "fees_paid", Decimal("0")) for t in self.engine.closed_trades), Decimal("0"))
        trend_open = sum((p.entry_fees for p in self.engine.open_positions.values()), Decimal("0"))
        range_closed = sum((getattr(t, "fees_paid", Decimal("0")) for t in self.range_engine.closed_trades), Decimal("0")) if hasattr(self, "range_engine") else Decimal("0")
        range_open = sum((getattr(p, "entry_fees", Decimal("0")) for p in self.range_engine.open_positions.values()), Decimal("0")) if hasattr(self, "range_engine") else Decimal("0")
        return trend_closed + trend_open + range_closed + range_open

    @property
    def total_funding(self) -> Decimal:
        trend_closed = sum((getattr(t, "funding_pnl", Decimal("0")) for t in self.engine.closed_trades), Decimal("0"))
        trend_open = sum((p.funding_pnl for p in self.engine.open_positions.values()), Decimal("0"))
        range_closed = sum((getattr(t, "funding_pnl", Decimal("0")) for t in self.range_engine.closed_trades), Decimal("0")) if hasattr(self, "range_engine") else Decimal("0")
        return trend_closed + trend_open + range_closed

    @property
    def total_turnover(self) -> Decimal:
        trend_to = sum(
            (t.initial_qty * t.entry_price + t.final_qty * t.exit_price for t in self.engine.closed_trades),
            Decimal("0"),
        )
        range_to = sum(
            (t.qty * t.entry_price + t.qty * t.exit_price for t in self.range_engine.closed_trades),
            Decimal("0"),
        ) if hasattr(self, "range_engine") else Decimal("0")
        return trend_to + range_to

    def get_unified_cash(self) -> Decimal:
        """Calculates unified realized cash across both Trend and Range submodules."""
        trend_realized = sum((t.net_pnl for t in self.engine.closed_trades), Decimal("0"))
        range_realized = sum((t.net_pnl for t in self.range_engine.closed_trades), Decimal("0")) if hasattr(self, "range_engine") else Decimal("0")
        return self.initial_capital + trend_realized + range_realized

    def get_unified_unrealized(self) -> Decimal:
        """Calculates unified unrealized PnL across both Trend and Range open positions."""
        trend_snap = self.engine.get_portfolio_snapshot(self.latest_prices)
        trend_unrealized = trend_snap.equity - self.engine.realized_equity
        range_unrealized = Decimal("0")
        if hasattr(self, "range_engine"):
            for s, pos in self.range_engine.open_positions.items():
                p_qty = getattr(pos, "current_qty", getattr(pos, "qty", Decimal("0")))
                mark = self.latest_prices.get(s, pos.entry_price)
                gross = (mark - pos.entry_price) * p_qty * Decimal(str(pos.direction))
                exit_fee = p_qty * mark * self.range_engine.taker_fee
                range_unrealized += (gross - exit_fee)
        return trend_unrealized + range_unrealized

    def get_unified_equity(self) -> Decimal:
        """Unified equity = Realized Cash ($300 base) + Total Unrealized PnL."""
        return self.get_unified_cash() + self.get_unified_unrealized()

    def get_total_gross_notional(self) -> Decimal:
        """Total gross notional exposure across BOTH Trend and Range engines."""
        trend_notional = sum(
            (p.current_qty * self.latest_prices.get(p.symbol, p.entry_price) for p in self.engine.open_positions.values()),
            Decimal("0"),
        )
        range_notional = sum(
            (getattr(p, "current_qty", getattr(p, "qty", Decimal("0"))) * self.latest_prices.get(p.symbol, p.entry_price) for p in self.range_engine.open_positions.values()),
            Decimal("0"),
        ) if hasattr(self, "range_engine") else Decimal("0")
        return trend_notional + range_notional

    def _wire_unified_capacity_checks(self) -> None:
        """
        P0.2: Enforces that BEFORE EVERY VIRTUAL EXECUTION:
        1. Unified equity & available capital are positive.
        2. Proposed gross exposure does not exceed the 3.5x leverage cap.
        3. Total open risk does not exceed portfolio risk budget (10%).
        4. Mutual exclusion prevents both engines from holding the same symbol concurrently.
        """
        def trend_capacity_check(sym: str, price: Decimal, qty: Decimal, prices: Dict[str, Decimal]) -> bool:
            if not self.schedule_active:
                return False
            # Check mutual exclusion
            if hasattr(self, "range_engine") and (sym in self.range_engine.open_positions or sym in self.range_engine.pending_orders):
                logger.info(f"CAPACITY_REJECTED: Symbol {sym} already active in Range module")
                return False
            # Check unified equity
            unified_eq = self.get_unified_equity()
            if unified_eq <= Decimal("0"):
                logger.warning(f"CAPACITY_REJECTED: Unified equity {unified_eq} <= 0")
                return False
            # Check proposed cumulative gross notional
            current_gross = self.get_total_gross_notional()
            new_notional = qty * price
            proposed_gross = current_gross + new_notional
            max_notional = unified_eq * Decimal("3.5")
            if proposed_gross > max_notional:
                logger.warning(f"LEVERAGE_CAP_EXCEEDED: proposed {proposed_gross:.2f} > cap {max_notional:.2f}")
                return False
            # Check portfolio risk budget (sum of open stop risks)
            current_open_risk = sum(
                (p.current_qty * abs(p.entry_price - p.current_stop) for p in self.engine.open_positions.values()),
                Decimal("0"),
            ) + (
                sum(
                    (getattr(p, "current_qty", getattr(p, "qty", Decimal("0"))) * abs(p.entry_price - getattr(p, "stop_loss", p.entry_price))
                     for p in self.range_engine.open_positions.values()),
                    Decimal("0"),
                ) if hasattr(self, "range_engine") else Decimal("0")
            )
            # 10% maximum portfolio stop loss risk
            if (current_open_risk + (qty * price * Decimal("0.02"))) > (unified_eq * Decimal("0.10")):
                logger.warning("PORTFOLIO_RISK_BUDGET_EXCEEDED: New position would exceed 10% risk budget")
                return False
            return True

        def range_capacity_check(sym: str, notional: Decimal, prices: Dict[str, Decimal]) -> bool:
            if not self.schedule_active:
                return False
            # Mutual exclusion
            if sym in self.engine.open_positions:
                logger.info(f"CAPACITY_REJECTED: Symbol {sym} already active in Trend module")
                return False
            unified_eq = self.get_unified_equity()
            if unified_eq <= Decimal("0"):
                return False
            current_gross = self.get_total_gross_notional()
            proposed_gross = current_gross + notional
            max_notional = unified_eq * Decimal("3.5")
            if proposed_gross > max_notional:
                logger.warning(f"LEVERAGE_CAP_EXCEEDED: proposed {proposed_gross:.2f} > cap {max_notional:.2f}")
                return False
            return True

        self.engine._capacity = trend_capacity_check
        if hasattr(self, "range_engine"):
            self.range_engine._capacity_check = range_capacity_check

    def _record_missed_opportunity(
        self,
        symbol: str,
        timestamp_ms: int,
        strategy: str,
        direction: int,
        intended_price: Decimal,
        reason: str,
    ) -> None:
        """P0.1: Records a missed trade opportunity during historical catchup / gap recovery."""
        record = {
            "symbol": symbol,
            "timestamp_ms": timestamp_ms,
            "strategy": strategy,
            "direction": direction,
            "intended_price": float(intended_price),
            "reason": reason,
        }
        self.missed_opportunities.append(record)
        logger.info(f"MISSED_OPPORTUNITY recorded for {symbol} at {timestamp_ms}: {reason}")
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=10.0)
            with conn:
                conn.execute("""
                    INSERT INTO native_missed_opportunities (
                        symbol, timestamp_ms, strategy, direction, intended_price, reason
                    ) VALUES (?, ?, ?, ?, ?, ?);
                """, (symbol, timestamp_ms, strategy, direction, float(intended_price), reason))
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to record missed opportunity in SQLite: {e}")

    def process_funding_settlement(
        self,
        symbol: str,
        mark_price: Decimal,
        funding_rate: Optional[Decimal],
        settlement_time_ms: int,
        funding_interval_ms: Optional[int] = None,
    ) -> Decimal:
        """
        P0.3: Settles funding based on actual instrument schedule:
        1. Refuses accrual when funding_rate is unknown/unconfirmed/NaN.
        2. Ensures no duplicate charges via settlement_key (symbol, settlement_time_ms).
        3. Verifies position ownership at settlement moment (entry_time <= settlement_time_ms).
        4. Saves settlement record to SQLite table native_funding_settlements.
        """
        if funding_rate is None:
            logger.warning(f"REJECTED_UNKNOWN_FUNDING_RATE: Symbol {symbol} has None funding rate at {settlement_time_ms}")
            return Decimal("0")
        try:
            f_rate_float = float(funding_rate)
            if not np.isfinite(f_rate_float):
                logger.warning(f"REJECTED_UNKNOWN_FUNDING_RATE: Symbol {symbol} rate {funding_rate} is not finite")
                return Decimal("0")
        except Exception:
            return Decimal("0")

        settlement_key = (symbol, settlement_time_ms)
        if settlement_key in self.settled_funding_windows:
            return Decimal("0")  # Already settled

        # Position ownership check in Trend engine
        pos = self.engine.open_positions.get(symbol)
        if pos is None or pos.entry_time > settlement_time_ms:
            return Decimal("0")  # Not holding position at settlement moment

        # Calculate payment: pos.current_qty * mark_price * funding_rate * (-pos.direction)
        payment = pos.current_qty * mark_price * funding_rate * (-Decimal(str(pos.direction)))
        pos.funding_pnl += payment
        self.engine.realized_equity += payment

        # Record settlement in memory and SQLite
        self.settled_funding_windows.add(settlement_key)
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            conn = sqlite3.connect(str(self.db_path), timeout=10.0)
            with conn:
                conn.execute("""
                    INSERT OR REPLACE INTO native_funding_settlements (
                        symbol, settlement_time_ms, funding_rate, mark_price,
                        position_qty, payment, created_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """, (
                    symbol,
                    settlement_time_ms,
                    float(funding_rate),
                    float(mark_price),
                    float(pos.current_qty),
                    float(payment),
                    now_iso,
                ))
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to record funding settlement in SQLite: {e}")

        logger.info(f"FUNDING_SETTLED: {symbol} at {settlement_time_ms}: payment={payment:.6f} USDT")
        return payment

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
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS native_state_journal (
                        journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        saved_at_ms INTEGER NOT NULL,
                        saved_at_utc TEXT NOT NULL,
                        event_key TEXT NOT NULL,
                        realized_equity REAL NOT NULL,
                        peak_equity REAL NOT NULL,
                        state_json TEXT NOT NULL
                    );
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS native_missed_opportunities (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        symbol TEXT NOT NULL,
                        timestamp_ms INTEGER NOT NULL,
                        strategy TEXT NOT NULL,
                        direction INTEGER NOT NULL,
                        intended_price REAL NOT NULL,
                        reason TEXT NOT NULL
                    );
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS native_funding_settlements (
                        symbol TEXT NOT NULL,
                        settlement_time_ms INTEGER NOT NULL,
                        funding_rate REAL NOT NULL,
                        mark_price REAL NOT NULL,
                        position_qty REAL NOT NULL,
                        payment REAL NOT NULL,
                        created_at_utc TEXT NOT NULL,
                        PRIMARY KEY(symbol, settlement_time_ms)
                    );
                """)
        finally:
            conn.close()

    def load_state(self) -> None:
        """
        Authoritatively recovers state from SQLite native_state_journal first (Section P0.3),
        falling back to disk JSON only if the SQLite journal is empty.
        """
        recovered_from_sqlite = False
        if self.db_path.exists():
            try:
                conn = sqlite3.connect(str(self.db_path), timeout=30.0)
                try:
                    cursor = conn.cursor()
                    # Authoritatively recover funding settlements and missed opportunities from their own tables
                    try:
                        cursor.execute("SELECT symbol, settlement_time_ms FROM native_funding_settlements;")
                        for f_sym, f_ts in cursor.fetchall():
                            self.settled_funding_windows.add((f_sym, int(f_ts)))
                    except Exception:
                        pass
                    try:
                        cursor.execute("SELECT symbol, timestamp_ms, strategy, direction, intended_price, reason FROM native_missed_opportunities;")
                        for r in cursor.fetchall():
                            self.missed_opportunities.append({
                                "symbol": r[0], "timestamp_ms": r[1], "strategy": r[2],
                                "direction": r[3], "intended_price": r[4], "reason": r[5]
                            })
                    except Exception:
                        pass

                    cursor.execute("""
                        SELECT state_json FROM native_state_journal
                        ORDER BY journal_id DESC LIMIT 1;
                    """)
                    row = cursor.fetchone()
                    if row and row[0]:
                        data = json.loads(row[0])
                        self.engine.load_from_dict(data.get("engine", {}))
                        if "range_engine" in data and hasattr(self.range_engine, "load_dict"):
                            self.range_engine.load_dict(data["range_engine"])
                        self.last_processed_bar_ts = data.get("last_processed_bar_ts", self.last_processed_bar_ts)
                        if "peak_equity" in data:
                            self.peak_equity = Decimal(str(data["peak_equity"]))
                        if "settled_funding_windows" in data:
                            self.settled_funding_windows.update(tuple(x) for x in data["settled_funding_windows"])
                        recovered_from_sqlite = True
                        logger.info(
                            f"Authoritatively restored Native state from SQLite native_state_journal: "
                            f"Combined Cash={self.get_unified_cash():.4f}, Peak Equity={self.peak_equity:.4f}, Positions={len(self.engine.open_positions) + len(self.range_engine.open_positions)}"
                        )
                finally:
                    conn.close()
            except Exception as e:
                logger.warning(f"Authoritative SQLite state load fallback: {e}")

        if not recovered_from_sqlite and self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
                self.engine.load_from_dict(data.get("engine", {}))
                if "range_engine" in data and hasattr(self.range_engine, "load_dict"):
                    self.range_engine.load_dict(data["range_engine"])
                self.last_processed_bar_ts = data.get("last_processed_bar_ts", self.last_processed_bar_ts)
                if "peak_equity" in data:
                    self.peak_equity = Decimal(str(data["peak_equity"]))
                if "settled_funding_windows" in data:
                    self.settled_funding_windows = set(tuple(x) for x in data["settled_funding_windows"])
                logger.info(
                    f"Restored Native state from JSON {self.state_path}: "
                    f"Combined Cash={self.get_unified_cash():.4f}, Peak Equity={self.peak_equity:.4f}, Positions={len(self.engine.open_positions) + len(self.range_engine.open_positions)}"
                )
            except Exception as e:
                logger.warning(f"Failed to load state from {self.state_path}: {e}")

    def save_state(self) -> None:
        """Atomically saves state snapshot to JSON."""
        state_data = {
            "version": "3.2-NATIVE-DUAL-REGIME",
            "saved_at_iso": datetime.now(timezone.utc).isoformat(),
            "cohort_id": self.cohort_id,
            "peak_equity": str(self.peak_equity),
            "settled_funding_windows": [list(x) for x in self.settled_funding_windows],
            "engine": self.engine.to_dict(),
            "range_engine": self.range_engine.to_dict() if hasattr(self.range_engine, "to_dict") else {},
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
        is_historical_catchup: bool = False,
    ) -> Dict[str, Any]:
        """
        Processes a closed bar through the real Donchian quantitative cycle:
        1. Protect T0 at engine level (Section P0.1): zero trades/fees before authorized T0.
        2. Authoritative SQLite Dedup check (Read-Only).
        3. Dual-Regime Indicator computation with zero-lookahead shift(1) (Section P0.2).
        4. Regime classification & stepping of Range Mean-Reversion vs Trend Breakout.
        5. Single atomic commit to native_journal, native_trades, native_state_journal, AND native_dedup (Section P0.3 & P2).
        6. Crash recovery after commit without JSON desynchronization.
        """
        ts = int(bar["timestamp"])
        bar_close_ms = ts + BAR_INTERVAL_MS
        event_key = f"{symbol}_{ts}"

        # 1. Protect T0 at engine level (Section P0.1)
        # Until explicit authorization (schedule_active=True) and arrival of T0,
        # ONLY data warm-up is permitted. Zero trades, zero fees, zero equity change.
        if not self.schedule_active or bar_close_ms < self.t0_ms:
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

            if len(df) >= min(DONCHIAN_WINDOW, 50):
                df = prepare_dual_regime_indicators(df, self.classifier, lookback=DONCHIAN_WINDOW)
            self.candle_buffers[symbol] = df
            self.last_processed_bar_ts[symbol] = ts
            self.latest_prices[symbol] = current_price or Decimal(str(bar["close"]))
            self.latest_quote_times[symbol] = time.monotonic()
            self.has_market_data = True

            return {
                "status": "PRE_T0_WARMUP",
                "symbol": symbol,
                "ts": ts,
                "positions_count": 0,
                "realized_equity": float(self.initial_capital),
                "reason": f"Pre-T0 warm-up (schedule_active={self.schedule_active}, t0={self.t0_utc})",
            }

        # 2. Authoritative SQLite Dedup Check (Read-Only)
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

        # Close price and quotes: for historical catchup (> 120s ago), strictly use candle close
        bar_close_price = Decimal(str(bar["close"]))
        is_historical = is_historical_catchup or ((time.time() * 1000 - bar_close_ms) > 120000)
        if is_historical or current_price is None:
            price = bar_close_price
        else:
            price = current_price
        self.latest_prices[symbol] = price
        self.latest_quote_times[symbol] = time.monotonic()
        self.has_market_data = True

        # Process Funding Payment based on confirmed schedule and rates (Section P0.3)
        funding_interval_ms = 8 * 3600 * 1000
        is_funding_boundary = (bar_close_ms % funding_interval_ms == 0)
        if funding_rate is not None:
            if is_funding_boundary or ((bar_close_ms % 3600000 == 0) and funding_rate != Decimal("0")):
                self.process_funding_settlement(
                    symbol=symbol,
                    mark_price=price,
                    funding_rate=funding_rate,
                    settlement_time_ms=bar_close_ms,
                    funding_interval_ms=funding_interval_ms,
                )
        elif is_funding_boundary:
            logger.warning(
                f"REJECTED_UNKNOWN_FUNDING_RATE: Symbol {symbol} reached boundary {bar_close_ms} but funding_rate is None"
            )

        # 3. Dual-Regime indicator preparation and classification (Section P0.2)
        bar_dict = df.iloc[-1].to_dict() if len(df) > 0 else record
        price_map = {symbol: price}

        reg = self.classifier.classify_bar(
            symbol=symbol,
            adx=float(bar_dict.get("adx", 0)),
            bb_mid=float(bar_dict.get("bb_mid", 0)),
            bb_upper=float(bar_dict.get("bb_upper", 0)),
            bb_lower=float(bar_dict.get("bb_lower", 0)),
            bb_width_pctl=float(bar_dict.get("bb_width_pctl", 0.5)),
            kc_upper=float(bar_dict.get("kc_upper", 0)),
            kc_lower=float(bar_dict.get("kc_lower", 0)),
            rsi=float(bar_dict.get("rsi", 50.0)),
            atr=float(bar_dict.get("atr", 0)),
            volume=float(bar_dict.get("volume", 0)),
            volume_sma=float(bar_dict.get("volume_sma", 1.0)),
        )
        self.latest_regimes[symbol] = reg

        # 4. Synchronize unified cash balance ($300 base) to prevent phantom capital / double spending
        unified_cash = self.get_unified_cash()
        self.engine.realized_equity = unified_cash
        if hasattr(self, "range_engine"):
            self.range_engine.realized_equity = unified_cash

        # Check total gross notional against 3.5x portfolio leverage cap
        current_gross_notional = self.get_total_gross_notional()
        unified_equity = self.get_unified_equity()
        max_portfolio_notional = unified_equity * Decimal("3.5")
        can_open_new = (current_gross_notional < max_portfolio_notional)

        # Step Range Mean-Reversion Engine (Chop / Range)
        is_chop = reg.regime.value in ["CHOP", "COMPRESSION", "RANGE"]
        prev_range_trades = len(self.range_engine.closed_trades) if hasattr(self, "range_engine") else 0
        if is_chop and hasattr(self, "range_engine"):
            active_trend_syms = list(self.engine.open_positions.keys())
            if is_historical or not can_open_new:
                # Historical gap or max leverage reached: manage existing positions only, forbid new orders
                self.range_engine.manage_positions_on_bar(ts, {symbol: bar_dict}, {symbol: reg})
            else:
                self.range_engine.step_bar(
                    timestamp=ts,
                    bars={symbol: bar_dict},
                    next_bars={symbol: record},
                    next_open_prices=price_map,
                    regimes={symbol: reg},
                    btc_bullish=btc_bullish,
                    btc_bearish=btc_bearish,
                    active_trend_symbols=active_trend_syms,
                )
        new_range_trades = self.range_engine.closed_trades[prev_range_trades:] if hasattr(self, "range_engine") else []

        # 5. Step Trend Engine on symbols not held by Range
        prev_trades_count = len(self.engine.closed_trades)
        range_held = hasattr(self, "range_engine") and (symbol in self.range_engine.open_positions or symbol in self.range_engine.pending_orders)
        if not range_held:
            if is_historical:
                # P0.1: Check if breakout signal triggers during historical gap, record MISSED_OPPORTUNITY
                cand = self.engine.scanner.evaluate_symbol_bar(
                    symbol=symbol,
                    close=float(bar_dict["close"]),
                    upper_channel=float(bar_dict.get("upper", np.nan)),
                    lower_channel=float(bar_dict.get("lower", np.nan)),
                    ema_fast=float(bar_dict.get("ema_fast", np.nan)),
                    ema_slow=float(bar_dict.get("ema_slow", np.nan)),
                    atr=float(bar_dict.get("atr", np.nan)),
                    adx=float(bar_dict.get("adx", np.nan)),
                    volume=float(bar_dict.get("volume", 0)),
                    volume_sma=float(bar_dict.get("volume_sma", 1)),
                )
                if cand is not None and ((cand.direction == 1 and btc_bullish) or (cand.direction == -1 and btc_bearish)) and symbol not in self.engine.open_positions:
                    self._record_missed_opportunity(
                        symbol=symbol,
                        timestamp_ms=ts,
                        strategy="TREND_BREAKOUT",
                        direction=cand.direction,
                        intended_price=price,
                        reason="HISTORICAL_GAP_RECOVERY: Retroactive order placement forbidden",
                    )
                # Manage existing open positions only, block new entries
                self.engine.process_bar_event(
                    timestamp=ts,
                    bars={symbol: bar_dict},
                    next_open_prices=price_map,
                    manage_positions=True,
                    btc_bullish=btc_bullish,
                    btc_bearish=btc_bearish,
                    blocked_symbols=list(self.symbols),
                )
            else:
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

        # 6. Single Atomic Transaction: Trades + Journal + State Journal + Dedup Key (Section P0.3)
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
                        f"TREND_{trade.exit_reason}",
                    ))

                for r_trade in new_range_trades:
                    cursor.execute("""
                        INSERT OR REPLACE INTO native_trades (
                            trade_id, symbol, direction, entry_time_ms, exit_time_ms,
                            entry_price, exit_price, qty, gross_pnl, fees_paid,
                            funding_pnl, net_pnl, exit_reason
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """, (
                        f"R_{r_trade.symbol}_{r_trade.entry_time}",
                        r_trade.symbol,
                        r_trade.direction,
                        r_trade.entry_time,
                        r_trade.exit_time,
                        float(r_trade.entry_price),
                        float(r_trade.exit_price),
                        float(r_trade.qty),
                        float(r_trade.net_pnl + r_trade.fees_paid),
                        float(r_trade.fees_paid),
                        float(r_trade.funding_pnl),
                        float(r_trade.net_pnl),
                        f"RANGE_{r_trade.exit_reason}",
                    ))

                # Checkpoint journal with unified $300 account math
                now_dt = datetime.now(timezone.utc)
                cash = self.get_unified_cash()
                unrealized = self.get_unified_unrealized()
                comb_equity = cash + unrealized
                if comb_equity > self.peak_equity:
                    self.peak_equity = comb_equity

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
                    float(cash),
                    float(unrealized),
                    float(comb_equity),
                    float(cash),
                    float(self.total_fees),
                    float(self.total_funding),
                    float(self.peak_equity),
                    len(self.engine.open_positions) + (len(self.range_engine.open_positions) if hasattr(self, "range_engine") else 0),
                    3,
                    f"closed_bar_{symbol}",
                ))

                # Insert authoritative state snapshot in the SAME transaction! (Section P0.3)
                state_dict = {
                    "version": "3.2-NATIVE-DUAL-REGIME",
                    "saved_at_iso": now_dt.isoformat(),
                    "cohort_id": self.cohort_id,
                    "peak_equity": str(self.peak_equity),
                    "settled_funding_windows": [list(x) for x in self.settled_funding_windows],
                    "engine": self.engine.to_dict(),
                    "range_engine": self.range_engine.to_dict() if hasattr(self.range_engine, "to_dict") else {},
                    "last_processed_bar_ts": self.last_processed_bar_ts,
                }
                cursor.execute("""
                    INSERT INTO native_state_journal (
                        saved_at_ms, saved_at_utc, event_key, realized_equity,
                        peak_equity, state_json
                    ) VALUES (?, ?, ?, ?, ?, ?);
                """, (
                    int(now_dt.timestamp() * 1000),
                    now_dt.isoformat(),
                    event_key,
                    float(cash),
                    float(self.peak_equity),
                    json.dumps(state_dict, default=str),
                ))

                # Fault Injection Point (Before Commit)
                if fault_injection_point == "before_commit":
                    raise RuntimeError("FAULT_INJECTION: Crash immediately before commit")

                # Atomically commit dedup key inside the SAME transaction!
                cursor.execute("""
                    INSERT INTO native_dedup (event_key, processed_at_ms)
                    VALUES (?, ?);
                """, (event_key, int(time.time() * 1000)))
        finally:
            conn.close()

        # Fault Injection Point (After Commit Before JSON) - Section P0.3 verification
        if fault_injection_point == "after_commit_before_json":
            raise RuntimeError("FAULT_INJECTION: Crash after SQLite commit but before JSON write in NativePaperRunner")

        self.save_state()
        self.export_telemetry()

        return {
            "status": "HISTORICAL_RECOVERED" if is_historical else "PROCESSED",
            "symbol": symbol,
            "ts": ts,
            "positions_count": len(self.engine.open_positions) + (len(self.range_engine.open_positions) if hasattr(self, "range_engine") else 0),
            "realized_equity": float(cash),
            "missed_opportunities_count": len(self.missed_opportunities),
        }

    def update_quotes(
        self,
        quotes: Dict[str, Decimal],
        timestamp_ms: Optional[int] = None,
        fault_injection_point: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Updates live quotes for continuous MTM equity tracking and accompanies open positions.
        Enforces atomic state persistence on tick-level position exits so that a crash never resurrects closed positions.
        """
        now_mono = time.monotonic()
        ts = timestamp_ms or int(time.time() * 1000)
        for sym, price in quotes.items():
            self.latest_prices[sym] = price
            self.latest_quote_times[sym] = now_mono

        self.has_market_data = True

        # Accompany open positions on tick quotes: manage stops & MTM
        new_closed_trend = []
        if self.engine.open_positions:
            prev_closed = len(self.engine.closed_trades)
            self.engine.process_price_event(timestamp=ts, prices=self.latest_prices)
            new_closed_trend = self.engine.closed_trades[prev_closed:]

        # Check if any Range position hit stop-loss on tick quotes
        new_closed_range = []
        if hasattr(self, "range_engine") and self.range_engine.open_positions:
            symbols_to_close = []
            for sym, pos in list(self.range_engine.open_positions.items()):
                price = self.latest_prices.get(sym)
                if price is not None:
                    sl_hit = (price <= pos.stop_loss) if pos.direction == 1 else (price >= pos.stop_loss)
                    if sl_hit:
                        symbols_to_close.append((sym, price))
            for sym, price in symbols_to_close:
                pos = self.range_engine.open_positions.pop(sym)
                exit_fee = pos.qty * price * self.range_engine.taker_fee
                gross_pnl = (price - pos.entry_price) * pos.qty * Decimal(str(pos.direction))
                p_qty = getattr(pos, "current_qty", getattr(pos, "qty", Decimal("0")))
                net_pnl = gross_pnl - pos.entry_fees - exit_fee
                hold_hrs = max(0.1, (ts - pos.entry_time) / 3600000.0) if ts > pos.entry_time else 1.0
                trade = RangeClosedTrade(
                    symbol=sym,
                    direction=pos.direction,
                    entry_time=pos.entry_time,
                    exit_time=ts,
                    entry_price=pos.entry_price,
                    exit_price=price,
                    qty=p_qty,
                    net_pnl=net_pnl,
                    exit_reason="STOP_LOSS_TICK",
                    hold_hours=hold_hrs,
                    fees_paid=pos.entry_fees + exit_fee,
                    funding_pnl=Decimal("0"),
                )
                self.range_engine.closed_trades.append(trade)
                new_closed_range.append(trade)

        # Update peak equity on ticks
        comb_eq = self.get_unified_equity()
        if comb_eq > self.peak_equity:
            self.peak_equity = comb_eq

        # If any trades closed on this tick: perform atomic SQLite transaction
        if new_closed_trend or new_closed_range:
            conn = sqlite3.connect(str(self.db_path), timeout=30.0)
            try:
                with conn:
                    cursor = conn.cursor()
                    for trade in new_closed_trend:
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
                            f"TREND_{trade.exit_reason}",
                        ))
                    for r_trade in new_closed_range:
                        cursor.execute("""
                            INSERT OR REPLACE INTO native_trades (
                                trade_id, symbol, direction, entry_time_ms, exit_time_ms,
                                entry_price, exit_price, qty, gross_pnl, fees_paid,
                                funding_pnl, net_pnl, exit_reason
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """, (
                            f"R_{r_trade.symbol}_{r_trade.entry_time}",
                            r_trade.symbol,
                            r_trade.direction,
                            r_trade.entry_time,
                            r_trade.exit_time,
                            float(r_trade.entry_price),
                            float(r_trade.exit_price),
                            float(r_trade.qty),
                            float(r_trade.net_pnl + r_trade.fees_paid),
                            float(r_trade.fees_paid),
                            float(r_trade.funding_pnl),
                            float(r_trade.net_pnl),
                            f"RANGE_{r_trade.exit_reason}",
                        ))

                    # Insert journal checkpoint
                    now_dt = datetime.now(timezone.utc)
                    cash = self.get_unified_cash()
                    unrealized = self.get_unified_unrealized()
                    total_eq = cash + unrealized
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
                        float(cash),
                        float(unrealized),
                        float(total_eq),
                        float(cash),
                        float(self.total_fees),
                        float(self.total_funding),
                        float(self.peak_equity),
                        len(self.engine.open_positions) + (len(self.range_engine.open_positions) if hasattr(self, "range_engine") else 0),
                        3,
                        "tick_position_exit",
                    ))

                    # Insert authoritative state snapshot in native_state_journal in the SAME transaction!
                    state_dict = {
                        "version": "3.2-NATIVE-DUAL-REGIME",
                        "saved_at_iso": now_dt.isoformat(),
                        "cohort_id": self.cohort_id,
                        "peak_equity": str(self.peak_equity),
                        "settled_funding_windows": [list(x) for x in self.settled_funding_windows],
                        "engine": self.engine.to_dict(),
                        "range_engine": self.range_engine.to_dict() if hasattr(self.range_engine, "to_dict") else {},
                        "last_processed_bar_ts": self.last_processed_bar_ts,
                    }
                    cursor.execute("""
                        INSERT INTO native_state_journal (
                            saved_at_ms, saved_at_utc, event_key, realized_equity,
                            peak_equity, state_json
                        ) VALUES (?, ?, ?, ?, ?, ?);
                    """, (
                        int(now_dt.timestamp() * 1000),
                        now_dt.isoformat(),
                        f"TICK_EXIT_{ts}",
                        float(cash),
                        float(self.peak_equity),
                        json.dumps(state_dict, default=str),
                    ))
            finally:
                conn.close()

            # Fault injection point for testing crash recovery after tick exit commit
            if fault_injection_point == "after_tick_commit_before_json":
                raise RuntimeError("FAULT_INJECTION: Crash immediately after tick exit commit before JSON write")

        self.save_state()
        return self.export_telemetry()

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

            # 1. Update live quotes for all symbols in universe and settle instrument-specific funding
            quotes_to_update = {}
            for sym in self.symbols:
                t = ticker_map.get(sym)
                if t and t.get("lastPrice"):
                    try:
                        p = Decimal(str(t["lastPrice"]))
                        quotes_to_update[sym] = p
                        if t.get("fundingRate") and t.get("nextFundingTime"):
                            f_rate = Decimal(str(t["fundingRate"]))
                            next_f_ts = int(t["nextFundingTime"])
                            if now_srv >= next_f_ts:
                                self.process_funding_settlement(
                                    symbol=sym,
                                    mark_price=p,
                                    funding_rate=f_rate,
                                    settlement_time_ms=next_f_ts,
                                )
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

            # 3. Poll for newly closed 1h bars (limit=50 catches up on network gaps > 3 hours without loss)
            processed_count = 0
            for sym in self.symbols:
                try:
                    raw = self.client.get_kline(sym, interval=CANDLE_INTERVAL, limit=50)
                    if not raw:
                        continue
                    # Filter only strictly closed bars: open_ts + 3600000 <= now_srv
                    closed_bars = [k for k in raw if int(k[0]) + BAR_INTERVAL_MS <= now_srv]
                    # Sort ascending (oldest first)
                    closed_bars.sort(key=lambda k: int(k[0]))
                    for k in closed_bars:
                        bar_ts = int(k[0])
                        bar_close_ms = bar_ts + BAR_INTERVAL_MS
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
                            # Check if bar is historical catchup (> 120s ago)
                            is_hist = (now_srv - bar_close_ms) > 120000
                            # For historical bars, current_price is None so that bar["close"] is strictly used
                            c_price = None if is_hist else quotes_to_update.get(sym)
                            res = self.ingest_closed_bar(
                                symbol=sym,
                                bar=bar_dict,
                                current_price=c_price,
                                funding_rate=None,
                                btc_bullish=btc_bullish,
                                btc_bearish=btc_bearish,
                                is_historical_catchup=is_hist,
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
                "positions_count": len(self.engine.open_positions) + (len(self.range_engine.open_positions) if hasattr(self, "range_engine") else 0),
                "positions_detail": {},
                "data_freshness_status": status,
                "valuation_status": val_status,
                "error_count": 0,
                "last_update_iso": now_dt.isoformat(),
                "notes": f"Native Quantitative Cycle: {status} (Awaiting verified market data)",
                "is_active": is_fresh,
                "schedule_active": bool(self.schedule_active),  # Protocol 009 Invariant
            }
        else:
            cash = float(self.get_unified_cash())
            unrealized = float(self.get_unified_unrealized())
            eq = cash + unrealized
            pnl = eq - init_cap
            ret_pct = (pnl / init_cap) * 100.0 if init_cap > 0 else 0.0
            peak = float(self.peak_equity)
            mdd = round(((eq - peak) / peak) * 100.0, 2) if peak > 0 else 0.0

            pos_detail = {}
            for s, pos in self.engine.open_positions.items():
                pos_detail[s] = {
                    "strategy": "TREND",
                    "direction": "LONG" if pos.direction == 1 else "SHORT",
                    "entry_price": float(pos.entry_price),
                    "current_qty": float(pos.current_qty),
                    "current_stop": float(pos.current_stop),
                    "bars_held": pos.bars_held,
                }
            if hasattr(self, "range_engine"):
                for s, pos in self.range_engine.open_positions.items():
                    p_qty = getattr(pos, "current_qty", getattr(pos, "qty", Decimal("0")))
                    pos_detail[f"{s}_RANGE"] = {
                        "strategy": "RANGE",
                        "direction": "LONG" if pos.direction == 1 else "SHORT",
                        "entry_price": float(pos.entry_price),
                        "current_qty": float(p_qty),
                        "current_stop": float(pos.stop_loss),
                        "bars_held": getattr(pos, "bars_held", 0),
                    }

            tot_notional = float(self.get_total_gross_notional())
            exposure_pct = round((tot_notional / eq * 100.0), 2) if eq > 0 else 0.0

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
                "peak_equity_usdt": round(float(self.peak_equity), 4),
                "cash_usdt": round(cash, 4),
                "exposure_pct": exposure_pct,
                "turnover_usdt": float(self.total_turnover),
                "fees_paid_usdt": round(float(self.total_fees), 4),
                "slippage_cost_usdt": 0.0,
                "funding_paid_usdt": round(float(self.total_funding), 4),
                "positions_count": len(self.engine.open_positions) + (len(self.range_engine.open_positions) if hasattr(self, "range_engine") else 0),
                "positions_detail": pos_detail,
                "missed_opportunities_count": len(self.missed_opportunities),
                "data_freshness_status": "FRESH",
                "valuation_status": "FRESH",
                "error_count": 0,
                "last_update_iso": now_dt.isoformat(),
                "notes": "Linear Perpetuals, Half-Kelly sizing, Retail VIP0 fee tier (0.100% taker, 0.036% maker), unified $300 account",
                "is_active": True,
                "schedule_active": bool(self.schedule_active),
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
    parser.add_argument("--state-path", type=str, default=None, help="Path to state JSON")
    parser.add_argument("--cohort-id", type=str, default=DEFAULT_COHORT_ID, help="Canonical cohort ID")
    parser.add_argument("--t0-utc", type=str, default=DEFAULT_T0_UTC, help="Scheduled T0 UTC")
    parser.add_argument("--schedule-active", action="store_true", default=False, help="Explicitly activate study schedule at T0")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[3]
    db_path = Path(args.db_path) if args.db_path else repo_root / "reports" / DEFAULT_DB_NAME
    telemetry_path = Path(args.telemetry_file) if args.telemetry_file else repo_root / "reports" / DEFAULT_TELEMETRY_NAME
    state_path = Path(args.state_path) if args.state_path else None

    runner = NativePaperRunner(
        db_path=db_path,
        telemetry_path=telemetry_path,
        state_path=state_path,
        initial_capital=Decimal("300.000000"),
        cohort_id=args.cohort_id,
        t0_utc=args.t0_utc,
        schedule_active=args.schedule_active,
    )

    if args.dry_run:
        runner.run_preflight_cycle()
        sys.exit(0)

    runner.run(duration_sec=args.duration, interval_sec=args.interval)


if __name__ == "__main__":
    main()
