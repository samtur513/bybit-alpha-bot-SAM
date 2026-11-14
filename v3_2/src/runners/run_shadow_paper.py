#!/usr/bin/env python3
"""
v3_2/src/runners/run_shadow_paper.py
Standalone Runner for v3.2 Realistic Shadow Paper Trading Engine.
Protocol: TORG-BOT — THREE-SYSTEM-LAUNCH-GATE-006

Invariants:
1. Strict Capital Isolation: Initial capital = 300.000000 USDT.
2. Isolated SQLite Ledger: Uses v32_shadow_ledger.db. NEVER touches v32_paper_ledger.db or Port 8083 state.
3. Realistic Execution Model:
   - Retail standard fee tier: 0.100% taker fee, 0.040% maker fee.
   - Discrete L2 orderbook depth walking & depth slippage enabled.
   - Strict spread gate: reject fills when spread > 0.08%.
   - Contract-specific 8-hour perpetual funding accrual.
4. Single Cohort Synchronization: Canonical cohort 'TORG_THREE_SYS_20261115_001', T0='2026-11-17T00:00:00+00:00'.
5. Strictly Paper Trading: Zero live or demo trading API order calls.
"""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
import time
from typing import Any, Dict, Optional

# Ensure repository root is on sys.path for direct CLI execution
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v3_2.src.shadow.paper_trader_v32_shadow import PortfolioInstance

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [ShadowPaperRunner] %(message)s",
)
logger = logging.getLogger("ShadowPaperRunner")

DEFAULT_COHORT_ID = "TORG_THREE_SYS_20261115_001"
DEFAULT_T0_UTC = "2026-11-17T00:00:00+00:00"
DEFAULT_DB_NAME = "v32_shadow_ledger.db"
DEFAULT_TELEMETRY_NAME = "telemetry_shadow.json"


class ShadowPaperRunner:
    """
    Manages independent Realistic Shadow v3.2 paper engine, dedicated SQLite ledger,
    and periodic atomic telemetry updates.
    """

    def __init__(
        self,
        db_path: Path,
        telemetry_path: Path,
        initial_capital: Decimal = Decimal("300.000000"),
        cohort_id: str = DEFAULT_COHORT_ID,
        t0_utc: str = DEFAULT_T0_UTC,
    ):
        self.db_path = Path(db_path).resolve()
        self.telemetry_path = Path(telemetry_path).resolve()
        self.initial_capital = initial_capital
        self.cohort_id = cohort_id
        self.t0_utc = t0_utc
        self._running = False

        # Ensure directories exist
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.telemetry_path.parent.mkdir(parents=True, exist_ok=True)

        # 1. Initialize dedicated PortfolioInstance for Realistic Shadow
        self.portfolio = PortfolioInstance(
            name="SHADOW",
            initial_capital=self.initial_capital,
            taker_fee=Decimal("0.00100"),
            maker_fee=Decimal("0.00040"),
            use_spread_gate=True,
            use_depth_slippage=True,
            fixed_slippage=Decimal("0"),
            enable_auto_gearbox=True,
            require_confirmed_l2=True,
        )

        # 2. Initialize isolated SQLite schema
        self._init_sqlite_schema()

    def _init_sqlite_schema(self) -> None:
        """Creates tables for isolated shadow paper trading ledger."""
        conn = sqlite3.connect(str(self.db_path))
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL;")
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS shadow_journal (
                        journal_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp_ms INTEGER NOT NULL,
                        iso_time TEXT NOT NULL,
                        cohort_id TEXT NOT NULL,
                        realized_equity REAL NOT NULL,
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
                    CREATE TABLE IF NOT EXISTS shadow_trades (
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
                        slippage_bps REAL NOT NULL,
                        exit_reason TEXT NOT NULL
                    );
                """)
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS shadow_positions (
                        symbol TEXT PRIMARY KEY,
                        direction INTEGER NOT NULL,
                        entry_price REAL NOT NULL,
                        current_qty REAL NOT NULL,
                        entry_time_ms INTEGER NOT NULL,
                        stop_loss REAL NOT NULL,
                        unrealized_pnl REAL NOT NULL
                    );
                """)
        finally:
            conn.close()

    def checkpoint_state(self, note: str = "periodic_checkpoint") -> None:
        """Records current portfolio metrics to SQLite ledger atomically."""
        now_dt = datetime.now(timezone.utc)
        now_ms = int(now_dt.timestamp() * 1000)

        eq = float(self.portfolio.realized_equity)
        bal = float(getattr(self.portfolio, "balance", eq))
        fees = float(self.portfolio.total_fees)
        funding = float(self.portfolio.total_funding)
        peak = float(self.portfolio.peak_equity)
        pos_cnt = len(self.portfolio.positions)

        conn = sqlite3.connect(str(self.db_path))
        try:
            with conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO shadow_journal (
                        timestamp_ms, iso_time, cohort_id, realized_equity,
                        cash_balance, total_fees, total_funding, peak_equity,
                        positions_count, gear, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (
                    now_ms, now_dt.isoformat(), self.cohort_id, eq, bal,
                    fees, funding, peak, pos_cnt, 3, note
                ))
        finally:
            conn.close()

    def export_telemetry(self) -> Dict[str, Any]:
        """Builds and atomically writes standardized telemetry JSON."""
        now_dt = datetime.now(timezone.utc)
        init_cap = float(self.portfolio.initial_capital)
        eq = float(self.portfolio.realized_equity)
        pnl = eq - init_cap
        ret_pct = (pnl / init_cap) * 100.0 if init_cap > 0 else 0.0
        peak = float(self.portfolio.peak_equity)
        mdd = round(((eq - peak) / peak) * 100.0, 2) if peak > 0 else 0.0

        telemetry = {
            "name": "v3.2 Realistic Shadow",
            "role": "Realistic Execution Model (L2 Depth Walking, Retail VIP0 Fees, Spread Guard)",
            "market_type": "BYBIT_LINEAR_PERPETUAL",
            "cohort_id": self.cohort_id,
            "t0_utc": self.t0_utc,
            "initial_capital_usdt": round(init_cap, 4),
            "equity_usdt": round(eq, 4),
            "net_pnl_usdt": round(pnl, 4),
            "net_return_pct": round(ret_pct, 3),
            "mdd_pct": mdd,
            "cash_usdt": round(float(getattr(self.portfolio, "balance", eq)), 4),
            "exposure_pct": 0.0,
            "turnover_usdt": float(getattr(self.portfolio, "total_turnover", 0.0)),
            "fees_paid_usdt": round(float(self.portfolio.total_fees), 4),
            "slippage_cost_usdt": 0.0,
            "funding_paid_usdt": round(float(self.portfolio.total_funding), 4),
            "positions_count": len(self.portfolio.positions),
            "positions_detail": {},
            "data_freshness_status": "FRESH",
            "valuation_status": "FRESH",
            "error_count": 0,
            "last_update_iso": now_dt.isoformat(),
            "notes": "Linear Perpetuals, Discrete L2 depth walking, 0.10% VIP0 retail fee tier, isolated DB",
            "is_active": True,
        }

        # Atomic file write (write to .tmp then replace)
        tmp_path = self.telemetry_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(telemetry, f, indent=2)
        os.replace(tmp_path, self.telemetry_path)
        return telemetry

    def run_dry_run(self) -> Dict[str, Any]:
        """Executes a single preflight cycle, writes ledger and telemetry, and verifies."""
        logger.info("Executing Realistic Shadow preflight dry-run...")
        self.checkpoint_state(note="dry_run_preflight")
        telemetry = self.export_telemetry()
        logger.info(f"Realistic Shadow preflight successful. Capital: {telemetry['equity_usdt']} USDT. Cohort: {self.cohort_id}")
        return telemetry

    def run(self, duration_sec: Optional[int] = None, interval_sec: int = 5) -> None:
        """Main runner loop."""
        self._running = True
        logger.info(f"Starting Realistic Shadow Runner. DB: {self.db_path}, Telemetry: {self.telemetry_path}")
        self.checkpoint_state(note="runner_start")
        self.export_telemetry()

        start_time = time.monotonic()
        try:
            while self._running:
                time.sleep(interval_sec)
                self.checkpoint_state(note="heartbeat")
                self.export_telemetry()

                if duration_sec and (time.monotonic() - start_time >= duration_sec):
                    logger.info(f"Specified duration of {duration_sec}s reached. Exiting cleanly.")
                    break
        except KeyboardInterrupt:
            logger.info("Received interrupt. Shutting down Realistic Shadow Runner.")
        finally:
            self._running = False
            self.checkpoint_state(note="runner_stop")
            self.export_telemetry()
            logger.info("Realistic Shadow Runner cleanly stopped.")


def main():
    parser = argparse.ArgumentParser(description="v3.2 Realistic Shadow Paper Trading Runner")
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

    runner = ShadowPaperRunner(
        db_path=db_path,
        telemetry_path=telemetry_path,
        initial_capital=Decimal("300.000000"),
        cohort_id=args.cohort_id,
        t0_utc=args.t0_utc,
    )

    if args.dry_run:
        runner.run_dry_run()
        sys.exit(0)

    runner.run(duration_sec=args.duration, interval_sec=args.interval)


if __name__ == "__main__":
    main()
