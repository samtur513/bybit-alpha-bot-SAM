#!/usr/bin/env python3
"""
tools/deploy_shadow_fleet_services.py

Deploys systemd service units for the Three-System Shadow Paper Fleet:
- bybit-shadow-native.service (System A)
- bybit-shadow-realistic.service (System B)
- bybit-shadow-r2.service (System C)
- bybit-shadow-dashboard.service (Unified Dashboard Port 8085)

Enforces AGENTS.md rules:
- EnvironmentFile=-...
- Explicit PYTHONPATH
- SO_REUSEADDR on HTTP server
- Zero interference with Port 8083 (bybit-bot-v32.service) or Xray VPN
"""

import os
import subprocess
import sys
from pathlib import Path

SYSTEMD_DIR = Path("/etc/systemd/system")

SERVICES = {
    "bybit-shadow-native.service": """[Unit]
Description=TORG-BOT v3.2 Native Paper Trading Runner (New Research Cohort)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/bybit-shadow/bybit-alpha-bot-SAM
EnvironmentFile=-/opt/bybit-shadow/bybit-alpha-bot-SAM/.env
Environment="PYTHONPATH=/opt/bybit-shadow/bybit-alpha-bot-SAM"
Environment="PYTHONUNBUFFERED=1"
Environment="PAPER_TRADING=true"
Environment="EQUITY_USDT=300"
ExecStart=/opt/bybit-bot/venv/bin/python v3_2/src/runners/run_native_paper.py --db-path /opt/bybit-shadow/data/native_ledger.db --telemetry-file /opt/bybit-shadow/telemetry/telemetry_native.json --state-path /opt/bybit-shadow/data/native_state.json --cohort-id 20261116_000000_THREE_SYSTEM_SHADOW --t0-utc 2026-11-16T00:00:00Z --schedule-active
Restart=always
RestartSec=10
LimitNOFILE=65535
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
""",

    "bybit-shadow-realistic.service": """[Unit]
Description=TORG-BOT v3.2 Realistic Shadow Paper Runner (L2 Depth & Spread Gate)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/bybit-shadow/bybit-alpha-bot-SAM
EnvironmentFile=-/opt/bybit-shadow/bybit-alpha-bot-SAM/.env
Environment="PYTHONPATH=/opt/bybit-shadow/bybit-alpha-bot-SAM"
Environment="PYTHONUNBUFFERED=1"
Environment="PAPER_TRADING=true"
Environment="EQUITY_USDT=300"
ExecStart=/opt/bybit-bot/venv/bin/python v3_2/src/runners/run_shadow_paper.py --db-path /opt/bybit-shadow/data/shadow_ledger.db --telemetry-file /opt/bybit-shadow/telemetry/telemetry_shadow.json --state-path /opt/bybit-shadow/data/shadow_state.json --cohort-id 20261116_000000_THREE_SYSTEM_SHADOW --t0-utc 2026-11-16T00:00:00Z --schedule-active
Restart=always
RestartSec=10
LimitNOFILE=65535
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
""",

    "bybit-shadow-r2.service": """[Unit]
Description=CryptoLab R2-SHADOW Authentic Observer (24 Profiles, Read-Only Public)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/bybit-shadow/CryptoLab/research/hunter001_dg001/r2_shadow
EnvironmentFile=-/opt/bybit-shadow/bybit-alpha-bot-SAM/.env
Environment="PYTHONPATH=/opt/bybit-shadow/CryptoLab/research/hunter001_dg001/r2_shadow:/opt/bybit-shadow/CryptoLab/research/hunter001_dg001/r2_validation:/opt/bybit-shadow/CryptoLab/research/hunter001_dg001/t01_profit_rank"
Environment="PYTHONUNBUFFERED=1"
Environment="OPENBLAS_NUM_THREADS=1"
Environment="OMP_NUM_THREADS=1"
Environment="MKL_NUM_THREADS=1"
ExecStart=/opt/bybit-bot/venv/bin/python runtime.py run --data-dir /opt/bybit-shadow/data/cryptolab --policy /opt/bybit-shadow/data/cryptolab/AUTHORIZATION.json
Restart=always
RestartSec=15
LimitNOFILE=65535
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
""",

    "bybit-shadow-dashboard.service": """[Unit]
Description=TORG-BOT Three-System Comparative Dashboard (0.0.0.0:8085)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/bybit-shadow/bybit-alpha-bot-SAM
EnvironmentFile=-/opt/bybit-shadow/bybit-alpha-bot-SAM/.env
Environment="PYTHONPATH=/opt/bybit-shadow/bybit-alpha-bot-SAM:/opt/bybit-shadow/CryptoLab"
Environment="PYTHONUNBUFFERED=1"
ExecStart=/opt/bybit-bot/venv/bin/python v3_2/src/runners/run_three_system_dashboard.py --host 0.0.0.0 --port 8085 --native-telemetry /opt/bybit-shadow/telemetry/telemetry_native.json --shadow-telemetry /opt/bybit-shadow/telemetry/telemetry_shadow.json --r2-telemetry /opt/bybit-shadow/telemetry/telemetry_r2.json --cohort-id 20261116_000000_THREE_SYSTEM_SHADOW --t0-utc 2026-11-16T00:00:00Z
Restart=always
RestartSec=10
LimitNOFILE=65535
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""
}


def main():
    if not SYSTEMD_DIR.exists():
        print(f"Error: {SYSTEMD_DIR} does not exist. Must be executed on Linux host.")
        sys.exit(1)

    print("Deploying systemd service units for Shadow Paper Fleet...")
    for unit_name, content in SERVICES.items():
        unit_path = SYSTEMD_DIR / unit_name
        unit_path.write_text(content, encoding="utf-8")
        print(f"  -> Written {unit_path}")

    print("\nReloading systemd daemon...")
    subprocess.run(["systemctl", "daemon-reload"], check=True)

    print("\nEnabling and starting services...")
    for unit_name in SERVICES:
        subprocess.run(["systemctl", "enable", unit_name], check=True)
        subprocess.run(["systemctl", "restart", unit_name], check=True)
        p = subprocess.run(["systemctl", "is-active", unit_name], capture_output=True, text=True)
        print(f"  -> {unit_name}: {p.stdout.strip()}")

    print("\nFLEET_DEPLOYMENT_COMPLETE: All 4 services deployed and started.")


if __name__ == "__main__":
    main()
