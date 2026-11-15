#!/usr/bin/env python3
"""
v3_2/src/runners/run_three_system_dashboard.py
Unified Standalone HTTP Daemon for TORG-BOT Three-System Comparative Dashboard (Port 8099).
Protocol: TORG-BOT — THREE-SYSTEM-LAUNCH-GATE-006

Invariants:
1. Unified Aggregation: Monitors System A (Native), System B (Shadow), and System C (CryptoLab R2).
2. Zero Fictitious Defaults: If an engine or telemetry source is absent, renders NOT_STARTED / UNKNOWN.
3. Strict Read-Only API: GET / serves HTML, GET /api/status serves JSON, GET /health serves health check.
   Any mutation request (POST/PUT/DELETE) is rejected with 405 Method Not Allowed.
4. Port & Socket Hygiene: Enforces SO_REUSEADDR (allow_reuse_address = True).
5. Isolation Invariant: Port 8083 (original production v3.2) remains running and completely untouched.
"""

import argparse
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
import threading
import time
from typing import Any, Dict, Optional

# Ensure repository root is on sys.path
REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v3_2.src.integrations.r2_consumer import R2TelemetryConsumer
from v3_2.src.integrations.three_system_dashboard import (
    ThreeSystemComparativeDashboard,
    create_dashboard_server,
    CANONICAL_COHORT_ID,
    CANONICAL_T0_UTC,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [ThreeSystemDashboard] %(message)s",
)
logger = logging.getLogger("ThreeSystemDashboard")

DEFAULT_PORT = 8099
DEFAULT_HOST = "127.0.0.1"
DEFAULT_NATIVE_FILE = REPO_ROOT / "reports" / "telemetry_native.json"
DEFAULT_SHADOW_FILE = REPO_ROOT / "reports" / "telemetry_shadow.json"
DEFAULT_R2_FILE = REPO_ROOT / "docs" / "integrations" / "r2_shadow" / "telemetry.json"


class FileBackedDashboardSupplier:
    """Supplies live comparative reports by reading telemetry JSON files from disk."""

    def __init__(
        self,
        native_file: Path,
        shadow_file: Path,
        r2_file: Path,
        cohort_id: str = CANONICAL_COHORT_ID,
        t0_utc: str = CANONICAL_T0_UTC,
    ):
        self.native_file = Path(native_file).resolve()
        self.shadow_file = Path(shadow_file).resolve()
        self.r2_file = Path(r2_file).resolve()
        self.cohort_id = cohort_id
        self.t0_utc = t0_utc
        self.r2_consumer = R2TelemetryConsumer()
        self.dashboard_core = ThreeSystemComparativeDashboard(
            r2_consumer=self.r2_consumer,
            r2_telemetry_path=self.r2_file,
            expected_cohort_id=cohort_id,
            expected_t0_utc=t0_utc,
        )

    def _read_json_file(self, path: Path) -> Optional[Dict[str, Any]]:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"Failed to read/parse {path}: {e}")
            return None

    def get_latest_report(self) -> Dict[str, Any]:
        """Reads latest files and generates comparative report."""
        now_dt = datetime.now(timezone.utc)
        native_raw = self._read_json_file(self.native_file)
        shadow_raw = self._read_json_file(self.shadow_file)

        # Build mock-like wrapper if raw JSON exists
        class FilePortfolioProxy:
            def __init__(self, d: Dict[str, Any]):
                self.realized_equity = d.get("equity_usdt")
                self.balance = d.get("cash_usdt")
                self.initial_capital = d.get("initial_capital_usdt", 300.0)
                self.total_fees = d.get("fees_paid_usdt", 0.0)
                self.total_funding = d.get("funding_paid_usdt", 0.0)
                self.total_turnover = d.get("turnover_usdt", 0.0)
                self.peak_equity = d.get("equity_usdt", 300.0)
                self.positions = d.get("positions_detail", {})
                self.cohort_id = d.get("cohort_id")
                self.t0_utc = d.get("t0_utc")
                self.status = d.get("status", "NOT_STARTED")
                self.last_update_iso = d.get("timestamp_utc") or d.get("last_update_iso")
                self.is_started = d.get("status") not in ("NOT_STARTED", "NO_DATA") and self.realized_equity is not None

        native_port = FilePortfolioProxy(native_raw) if native_raw else None
        shadow_port = FilePortfolioProxy(shadow_raw) if shadow_raw else None

        r2_result = None
        if self.r2_file.exists():
            r2_result = self.r2_consumer.read_telemetry_file(self.r2_file, now_dt=now_dt)

        return self.dashboard_core.generate_comparative_report(
            native_portfolio=native_port,
            shadow_portfolio=shadow_port,
            r2_result=r2_result,
            now_dt=now_dt,
            cohort_id=self.cohort_id,
            t0_utc=self.t0_utc,
        )


def main():
    parser = argparse.ArgumentParser(description="TORG-BOT Three-System Comparative Dashboard Daemon")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"HTTP port (default {DEFAULT_PORT})")
    parser.add_argument("--host", type=str, default=DEFAULT_HOST, help=f"HTTP host (default {DEFAULT_HOST})")
    parser.add_argument("--native-telemetry", type=str, default=str(DEFAULT_NATIVE_FILE), help="Native telemetry JSON path")
    parser.add_argument("--shadow-telemetry", type=str, default=str(DEFAULT_SHADOW_FILE), help="Shadow telemetry JSON path")
    parser.add_argument("--r2-telemetry", type=str, default=str(DEFAULT_R2_FILE), help="R2 telemetry JSON path")
    parser.add_argument("--cohort-id", type=str, default=CANONICAL_COHORT_ID, help="Canonical cohort ID")
    parser.add_argument("--t0-utc", type=str, default=CANONICAL_T0_UTC, help="Scheduled T0 UTC")
    parser.add_argument("--dry-run", action="store_true", help="Generate report once, verify and exit")
    parser.add_argument("--duration", type=int, default=None, help="Run duration in seconds")
    args = parser.parse_args()

    supplier = FileBackedDashboardSupplier(
        native_file=Path(args.native_telemetry),
        shadow_file=Path(args.shadow_telemetry),
        r2_file=Path(args.r2_telemetry),
        cohort_id=args.cohort_id,
        t0_utc=args.t0_utc,
    )

    if args.dry_run:
        logger.info("Executing Three-System Dashboard dry-run verification...")
        report = supplier.get_latest_report()
        html = supplier.dashboard_core.render_html_dashboard(report)
        logger.info(f"Report generated successfully. Target cohort: {report['target_cohort']['cohort_id']}")
        logger.info(f"System A (Native): {report['systems']['native']['data_freshness_status']} / Equity: {report['systems']['native']['equity_usdt']}")
        logger.info(f"System B (Shadow): {report['systems']['shadow']['data_freshness_status']} / Equity: {report['systems']['shadow']['equity_usdt']}")
        logger.info(f"System C (R2):     {report['systems']['r2_shadow']['data_freshness_status']} / Equity: {report['systems']['r2_shadow']['equity_usdt']}")
        logger.info(f"HTML rendered: {len(html)} bytes")
        sys.exit(0)

    server = create_dashboard_server(
        host=args.host,
        port=args.port,
        dashboard_provider=supplier.dashboard_core,
        report_supplier=supplier.get_latest_report,
    )

    logger.info(f"Three-System Comparative Dashboard HTTP server started on http://{args.host}:{args.port}")
    if args.duration:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        time.sleep(args.duration)
        server.shutdown()
        server.server_close()
        logger.info(f"Dashboard server cleanly stopped after {args.duration}s.")
    else:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            logger.info("Shutting down dashboard server.")
        finally:
            server.shutdown()
            server.server_close()
            logger.info("Dashboard server cleanly terminated.")


if __name__ == "__main__":
    main()
