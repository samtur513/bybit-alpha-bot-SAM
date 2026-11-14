"""
tests/test_three_system_launch_gate_006.py
Comprehensive End-to-End Local Integration Test Suite for TORG-BOT Three-System Cohort.
Protocol: TORG-BOT — THREE-SYSTEM-LAUNCH-GATE-006

Verifies:
1. Preflight dry-run of all 3 runner entrypoints.
2. Strict Capital Separation ($300.000000 independent capital, zero pooling).
3. Process fault isolation (crash/stoppage of one engine does not affect others).
4. Telemetry staleness guard (>45s max snapshot age).
5. Multi-process execution with --duration and clean shutdown.
6. Zero Live/Demo Trading API order invariant.
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v3_2.src.runners.run_native_paper import NativePaperRunner
from v3_2.src.runners.run_shadow_paper import ShadowPaperRunner
from v3_2.src.runners.run_r2_observer import R2ObserverRunner
from v3_2.src.runners.run_three_system_dashboard import FileBackedDashboardSupplier
from v3_2.src.integrations.r2_consumer import R2TelemetryConsumer
from v3_2.src.integrations.three_system_dashboard import (
    ThreeSystemComparativeDashboard,
    CANONICAL_COHORT_ID,
    CANONICAL_T0_UTC,
)


def test_runners_preflight_dry_run():
    """Verify all 3 runner entrypoints execute clean preflight dry-run."""
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        db_native = tmp_path / "native.db"
        tel_native = tmp_path / "native.json"
        runner_nat = NativePaperRunner(db_path=db_native, telemetry_path=tel_native)
        res_nat = runner_nat.run_dry_run()
        assert res_nat["equity_usdt"] == 300.0
        assert res_nat["cohort_id"] == CANONICAL_COHORT_ID
        assert tel_native.exists()
        assert db_native.exists()

        db_shadow = tmp_path / "shadow.db"
        tel_shadow = tmp_path / "shadow.json"
        runner_shad = ShadowPaperRunner(db_path=db_shadow, telemetry_path=tel_shadow)
        res_shad = runner_shad.run_dry_run()
        assert res_shad["equity_usdt"] == 300.0
        assert res_shad["cohort_id"] == CANONICAL_COHORT_ID
        assert tel_shadow.exists()
        assert db_shadow.exists()

        tel_r2 = tmp_path / "r2.json"
        runner_r2 = R2ObserverRunner(output_path=tel_r2)
        res_r2 = runner_r2.run_dry_run()
        assert tel_r2.exists()
        assert res_r2["system"] == "CRYPTOLAB_R2_SHADOW"


def test_capital_independence_and_no_pooling():
    """Verify that capital mutations in one system remain completely isolated from others."""
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        db_native = tmp_path / "native.db"
        tel_native = tmp_path / "native.json"
        runner_nat = NativePaperRunner(db_path=db_native, telemetry_path=tel_native)
        # Mutate Native: +$12.50
        runner_nat.portfolio.realized_equity = Decimal("312.50")
        runner_nat.portfolio.total_fees = Decimal("0.45")
        runner_nat.export_telemetry()

        db_shadow = tmp_path / "shadow.db"
        tel_shadow = tmp_path / "shadow.json"
        runner_shad = ShadowPaperRunner(db_path=db_shadow, telemetry_path=tel_shadow)
        # Mutate Shadow: -$5.20
        runner_shad.portfolio.realized_equity = Decimal("294.80")
        runner_shad.portfolio.total_fees = Decimal("0.80")
        runner_shad.export_telemetry()

        tel_r2 = tmp_path / "r2.json"
        runner_r2 = R2ObserverRunner(output_path=tel_r2)
        runner_r2.export_telemetry(equity_usdt=304.10)

        # Supply to Dashboard
        supplier = FileBackedDashboardSupplier(
            native_file=tel_native,
            shadow_file=tel_shadow,
            r2_file=tel_r2,
        )
        report = supplier.get_latest_report()

        sys_a = report["systems"]["native"]
        sys_b = report["systems"]["shadow"]
        sys_c = report["systems"]["r2_shadow"]

        assert sys_a["equity_usdt"] == 312.50
        assert sys_a["fees_paid_usdt"] == 0.45
        assert sys_a["net_pnl_usdt"] == 12.50

        assert sys_b["equity_usdt"] == 294.80
        assert sys_b["fees_paid_usdt"] == 0.80
        assert sys_b["net_pnl_usdt"] == -5.20

        assert sys_c["equity_usdt"] == 304.10
        assert sys_c["net_pnl_usdt"] == 4.10

        # Ensure spot-perp separation invariant is declared
        assert report["separation_rules"]["spot_perp_separation_enforced"] is True
        assert report["separation_rules"]["portfolio_pooling_forbidden"] is True


def test_process_fault_isolation_and_offline_status():
    """Verify that when one engine halts or its telemetry vanishes, others continue smoothly."""
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        tel_native = tmp_path / "native.json"
        tel_shadow = tmp_path / "shadow.json"
        tel_r2 = tmp_path / "r2.json"

        # Native is present
        runner_nat = NativePaperRunner(db_path=tmp_path / "nat.db", telemetry_path=tel_native)
        runner_nat.run_dry_run()

        # R2 is present
        runner_r2 = R2ObserverRunner(output_path=tel_r2)
        runner_r2.run_dry_run()

        # Shadow is absent (e.g. process crashed before writing or file deleted)
        if tel_shadow.exists():
            tel_shadow.unlink()

        supplier = FileBackedDashboardSupplier(
            native_file=tel_native,
            shadow_file=tel_shadow,
            r2_file=tel_r2,
        )
        report = supplier.get_latest_report()

        assert report["systems"]["native"]["data_freshness_status"] == "FRESH"
        assert report["systems"]["native"]["equity_usdt"] == 300.0

        # Shadow must be NOT_STARTED, None equity
        assert report["systems"]["shadow"]["data_freshness_status"] == "NOT_STARTED"
        assert report["systems"]["shadow"]["equity_usdt"] is None
        assert report["systems"]["shadow"]["valuation_status"] == "NOT_STARTED"

        # R2 continues unaffected
        assert report["systems"]["r2_shadow"]["data_freshness_status"] == "OBSERVING"
        assert report["systems"]["r2_shadow"]["equity_usdt"] == 300.0


def test_telemetry_staleness_detection():
    """Verify that telemetry older than 45s is marked NO_FRESH_HEARTBEAT and unvalued."""
    consumer = R2TelemetryConsumer(max_snapshot_age_sec=45.0, max_heartbeat_age_sec=45.0)

    # Generate telemetry dated 60s ago
    now_dt = datetime.now(timezone.utc)
    stale_dt = now_dt - timedelta(seconds=60)

    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        runner_r2 = R2ObserverRunner(output_path=tmp_path / "r2_stale.json")
        stale_payload = runner_r2.generate_payload(now_dt=stale_dt, equity_usdt=305.0)

        # Validate with now_dt as current evaluation time
        res = consumer.parse_and_validate(stale_payload, now_dt=now_dt)

        assert res.is_valid is True
        assert res.status == "STALE"
        assert res.valuation_status == "UNVALUED_OR_STALE"
        assert res.equity_usdt is None  # Fictitious equity must NOT be displayed!


def test_zero_live_orders_invariant():
    """Verify strict invariant that zero live or demo trading API orders were created."""
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        tel_r2 = tmp_path / "r2.json"
        runner_r2 = R2ObserverRunner(output_path=tel_r2)
        payload = runner_r2.export_telemetry()

        assert payload["real_orders"] == 0
        assert payload["demo_orders"] == 0

        consumer = R2TelemetryConsumer()
        res = consumer.parse_and_validate(payload)
        assert res.real_orders == 0
        assert res.demo_orders == 0


def test_e2e_subprocess_execution_with_duration():
    """Spawns all runners via subprocess for 2 seconds and verifies clean shutdown."""
    with tempfile.TemporaryDirectory() as td:
        tmp_path = Path(td)
        native_db = tmp_path / "e2e_native.db"
        native_tel = tmp_path / "e2e_native.json"
        shadow_db = tmp_path / "e2e_shadow.db"
        shadow_tel = tmp_path / "e2e_shadow.json"
        r2_tel = tmp_path / "e2e_r2.json"

        p_nat = subprocess.run(
            [sys.executable, "v3_2/src/runners/run_native_paper.py", "--duration", "2", "--interval", "1",
             "--db-path", str(native_db), "--telemetry-file", str(native_tel)],
            capture_output=True, text=True, timeout=15
        )
        assert p_nat.returncode == 0, f"Native runner failed: {p_nat.stderr}"

        p_shad = subprocess.run(
            [sys.executable, "v3_2/src/runners/run_shadow_paper.py", "--duration", "2", "--interval", "1",
             "--db-path", str(shadow_db), "--telemetry-file", str(shadow_tel)],
            capture_output=True, text=True, timeout=15
        )
        assert p_shad.returncode == 0, f"Shadow runner failed: {p_shad.stderr}"

        p_r2 = subprocess.run(
            [sys.executable, "v3_2/src/runners/run_r2_observer.py", "--duration", "2", "--interval", "1",
             "--output", str(r2_tel)],
            capture_output=True, text=True, timeout=15
        )
        assert p_r2.returncode == 0, f"R2 runner failed: {p_r2.stderr}"

        # Verify dashboard dry-run can ingest all 3 outputs
        p_dash = subprocess.run(
            [sys.executable, "v3_2/src/runners/run_three_system_dashboard.py", "--dry-run",
             "--native-telemetry", str(native_tel),
             "--shadow-telemetry", str(shadow_tel),
             "--r2-telemetry", str(r2_tel)],
            capture_output=True, text=True, timeout=15
        )
        assert p_dash.returncode == 0, f"Dashboard dry-run failed: {p_dash.stderr}"
        assert "Target cohort: TORG_THREE_SYS_20261115_001" in p_dash.stderr or "Target cohort: TORG_THREE_SYS_20261115_001" in p_dash.stdout

