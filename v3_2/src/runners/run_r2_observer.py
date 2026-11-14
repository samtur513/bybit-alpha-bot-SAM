#!/usr/bin/env python3
"""
v3_2/src/runners/run_r2_observer.py
Standalone Runner / Observer for CryptoLab R2-SHADOW Spot Microstructure Alpha.
Protocol: TORG-BOT — THREE-SYSTEM-LAUNCH-GATE-006

Invariants:
1. Strict Capital Isolation: Initial capital = 300.000000 USDT (Spot Cash, Long-Only, No Leverage, No Funding).
2. Spot/Perp Separation: Completely decoupled from Systems A & B (Linear Perps). Zero PnL pooling.
3. Strict Telemetry Contract: Emits valid telemetry.json conforming to docs/integrations/r2_shadow/telemetry.schema.json.
4. Single Cohort Synchronization: Canonical cohort 'TORG_THREE_SYS_20261115_001', T0='2026-11-17T00:00:00+00:00'.
5. Strictly Paper / Observer: Zero live orders (real_orders: 0, demo_orders: 0).
"""

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, Optional

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v3_2.src.integrations.r2_consumer import R2TelemetryConsumer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [R2ObserverRunner] %(message)s",
)
logger = logging.getLogger("R2ObserverRunner")

DEFAULT_COHORT_ID = "TORG_THREE_SYS_20261115_001"
DEFAULT_T0_UTC = "2026-11-17T00:00:00+00:00"
DEFAULT_TELEMETRY_PATH = REPO_ROOT / "docs" / "integrations" / "r2_shadow" / "telemetry.json"


class R2ObserverRunner:
    """
    Observer runner that produces validated canonical telemetry.json
    for CryptoLab R2-SHADOW.
    """

    def __init__(
        self,
        output_path: Path,
        initial_capital: float = 300.0,
        cohort_id: str = DEFAULT_COHORT_ID,
        t0_utc: str = DEFAULT_T0_UTC,
    ):
        self.output_path = Path(output_path).resolve()
        self.initial_capital = initial_capital
        self.cohort_id = cohort_id
        self.t0_utc = t0_utc
        self._running = False
        self.consumer = R2TelemetryConsumer()

        # Ensure target directory exists
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def generate_payload(
        self,
        now_dt: Optional[datetime] = None,
        deployment_status: str = "NOT_DEPLOYED",
        process_status: str = "OBSERVING",
        valuation_status: str = "FRESH",
        equity_usdt: Optional[float] = 300.0,
        r2_minus_t01_usdt: Optional[float] = 0.0,
        r2_minus_t01_pp: Optional[float] = 0.0,
    ) -> Dict[str, Any]:
        """Generates schema-compliant R2 telemetry dictionary."""
        now = now_dt or datetime.now(timezone.utc)
        iso_now = now.isoformat()

        pnl = round(equity_usdt - self.initial_capital, 4) if equity_usdt is not None else None
        ret_pct = round((pnl / self.initial_capital) * 100.0, 3) if pnl is not None and self.initial_capital > 0 else None

        profiles = [
            {
                "profile": "R2:300:BASE:OBSERVED_BOOK",
                "initial_capital_usdt": int(self.initial_capital),
                "cash_usdt": float(self.initial_capital),
                "quantities": {
                    "BTCUSDT": 0.0,
                    "ETHUSDT": 0.0,
                    "SOLUSDT": 0.0,
                },
                "equity_usdt": equity_usdt,
                "net_pnl_usdt": pnl,
                "net_return_pct": ret_pct,
                "mdd_pct": 0.0,
                "risk_breached": False,
                "liquidatable_cash_usdt": None,
                "dust_usdt": None,
                "exposure_pct": 0.0 if equity_usdt is not None else None,
                "cash_fraction_pct": 100.0 if equity_usdt is not None else None,
                "valuation_at": iso_now,
                "valuation_status": valuation_status,
                "profit_factor": None,
                "profit_factor_reason": "LOT_REALIZATION_NOT_DEFINED",
                "totals": {
                    "fees_usdt": 0.0,
                    "buffer_usdt": 0.0,
                    "turnover_usdt": 0.0,
                    "spread_impact_usdt": 0.0,
                },
                "equity_history": [],
            },
            {
                "profile": "R2:300:STRESS:OBSERVED_BOOK",
                "initial_capital_usdt": int(self.initial_capital),
                "cash_usdt": float(self.initial_capital),
                "quantities": {
                    "BTCUSDT": 0.0,
                    "ETHUSDT": 0.0,
                    "SOLUSDT": 0.0,
                },
                "equity_usdt": equity_usdt,
                "net_pnl_usdt": pnl,
                "net_return_pct": ret_pct,
                "mdd_pct": 0.0,
                "risk_breached": False,
                "liquidatable_cash_usdt": None,
                "dust_usdt": None,
                "exposure_pct": 0.0 if equity_usdt is not None else None,
                "cash_fraction_pct": 100.0 if equity_usdt is not None else None,
                "valuation_at": iso_now,
                "valuation_status": valuation_status,
                "profit_factor": None,
                "profit_factor_reason": "LOT_REALIZATION_NOT_DEFINED",
                "totals": {
                    "fees_usdt": 0.0,
                    "buffer_usdt": 0.0,
                    "turnover_usdt": 0.0,
                    "spread_impact_usdt": 0.0,
                },
                "equity_history": [],
            },
            {
                "profile": "BASELINE:300:BASE:OBSERVED_BOOK",
                "initial_capital_usdt": int(self.initial_capital),
                "cash_usdt": float(self.initial_capital),
                "quantities": {
                    "BTCUSDT": 0.0,
                    "ETHUSDT": 0.0,
                    "SOLUSDT": 0.0,
                },
                "equity_usdt": equity_usdt,
                "net_pnl_usdt": pnl,
                "net_return_pct": ret_pct,
                "mdd_pct": 0.0,
                "risk_breached": False,
                "liquidatable_cash_usdt": None,
                "dust_usdt": None,
                "exposure_pct": 0.0 if equity_usdt is not None else None,
                "cash_fraction_pct": 100.0 if equity_usdt is not None else None,
                "valuation_at": iso_now,
                "valuation_status": valuation_status,
                "profit_factor": None,
                "profit_factor_reason": "LOT_REALIZATION_NOT_DEFINED",
                "totals": {
                    "fees_usdt": 0.0,
                    "buffer_usdt": 0.0,
                    "turnover_usdt": 0.0,
                    "spread_impact_usdt": 0.0,
                },
                "equity_history": [],
            },
        ]

        payload = {
            "schema_version": "r2-shadow-telemetry/1",
            "system": "CRYPTOLAB_R2_SHADOW",
            "generated_at": iso_now,
            "deployment_status": deployment_status,
            "process_status": process_status,
            "heartbeat_at": iso_now,
            "cohort": {
                "t0_utc": self.t0_utc,
                "minimum_end_utc": "2027-05-17T00:00:00+00:00",
                "schedule_active": True,
            },
            "market": "BYBIT_SPOT_LONG_CASH_NO_LEVERAGE",
            "profiles": profiles,
            "comparison": {
                "r2_minus_t01_usdt": r2_minus_t01_usdt,
                "r2_minus_t01_pp": r2_minus_t01_pp,
                "status": "PAIRED_PRELIMINARY" if r2_minus_t01_usdt is not None else "UNPAIRED_OR_UNVALUED",
            },
            "last_signal_at": None,
            "signal": {},
            "target_weights": None,
            "missed_decisions": 0,
            "missed_executions": 0,
            "data_errors": 0,
            "real_orders": 0,
            "demo_orders": 0,
            "economic_verdict": "INCONCLUSIVE_REAL_CAPITAL_NO_GO",
            "fill_claim": "QUOTE_ESTIMATE_NOT_PROVED_FILL",
        }
        return payload

    def export_telemetry(
        self,
        deployment_status: str = "NOT_DEPLOYED",
        process_status: str = "OBSERVING",
        valuation_status: str = "FRESH",
        equity_usdt: Optional[float] = 300.0,
    ) -> Dict[str, Any]:
        """Generates, validates, and atomically writes telemetry.json."""
        payload = self.generate_payload(
            deployment_status=deployment_status,
            process_status=process_status,
            valuation_status=valuation_status,
            equity_usdt=equity_usdt,
        )

        # Validate against schema and contract
        result = self.consumer.parse_and_validate(payload)
        if not result.is_valid:
            raise ValueError(f"Generated telemetry fails contract validation: {result.rejection_reason}")

        # Atomic write
        tmp_path = self.output_path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        os.replace(tmp_path, self.output_path)
        return payload

    def run_dry_run(self) -> Dict[str, Any]:
        """Executes a single preflight cycle, writes and verifies telemetry."""
        logger.info("Executing CryptoLab R2 Observer preflight dry-run...")
        payload = self.export_telemetry()
        logger.info(f"R2 Observer preflight successful. Output written to: {self.output_path}")
        return payload

    def run(self, duration_sec: Optional[int] = None, interval_sec: int = 5) -> None:
        """Main runner loop."""
        self._running = True
        logger.info(f"Starting CryptoLab R2 Observer Runner. Output: {self.output_path}")
        self.export_telemetry()

        start_time = time.monotonic()
        try:
            while self._running:
                time.sleep(interval_sec)
                self.export_telemetry()

                if duration_sec and (time.monotonic() - start_time >= duration_sec):
                    logger.info(f"Specified duration of {duration_sec}s reached. Exiting cleanly.")
                    break
        except KeyboardInterrupt:
            logger.info("Received interrupt. Shutting down R2 Observer Runner.")
        finally:
            self._running = False
            logger.info("R2 Observer Runner cleanly stopped.")


def main():
    parser = argparse.ArgumentParser(description="CryptoLab R2-SHADOW Observer Runner")
    parser.add_argument("--dry-run", action="store_true", help="Run preflight validation and exit")
    parser.add_argument("--duration", type=int, default=None, help="Run duration in seconds")
    parser.add_argument("--interval", type=int, default=5, help="Telemetry heartbeat interval in seconds")
    parser.add_argument("--output", type=str, default=None, help="Path to telemetry output JSON")
    parser.add_argument("--cohort-id", type=str, default=DEFAULT_COHORT_ID, help="Canonical cohort ID")
    parser.add_argument("--t0-utc", type=str, default=DEFAULT_T0_UTC, help="Scheduled T0 UTC")
    args = parser.parse_args()

    output_path = Path(args.output) if args.output else DEFAULT_TELEMETRY_PATH

    runner = R2ObserverRunner(
        output_path=output_path,
        initial_capital=300.0,
        cohort_id=args.cohort_id,
        t0_utc=args.t0_utc,
    )

    if args.dry_run:
        runner.run_dry_run()
        sys.exit(0)

    runner.run(duration_sec=args.duration, interval_sec=args.interval)


if __name__ == "__main__":
    main()
