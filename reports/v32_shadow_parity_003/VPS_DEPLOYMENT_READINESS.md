# VPS_DEPLOYMENT_READINESS.md
## Protocol: V32-SHADOW-PARITY-003 — Safe VPS Deployment & Isolation Guide

- **Project**: TORG_BOT / Bybit Multi-Alpha v3.2 Dual-Regime Shadow
- **Target Host**: VPS `150.241.98.70`
- **Target Port**: **8084** (Shadow Dashboard) | **8083** (Original Untouched)
- **Service Unit**: `bybit-v32-shadow.service`
- **Verification Suite**: 53 / 53 tests passing (100%)
- **Status**: **READY FOR OPERATIONAL DEPLOYMENT**

---

## 1. Strict VPS Isolation Architecture

### 1.1 Non-Interference Invariants
To preserve historical continuity and avoid corrupting live data on VPS `150.241.98.70`:
1. **Port 8083 is Sacred & Untouched**:
   - `bybit-v32.service` running on Port 8083 must **NEVER** be stopped, restarted, reloaded, or modified.
   - Original state directories (`/opt/bybit-bot/v3_2/state/`, `/opt/bybit-bot/v3_2/data/`) are strictly read-only.
2. **Dedicated Shadow Port 8084**:
   - The Shadow Web Dashboard binds exclusively to `0.0.0.0:8084`.
   - `SO_REUSEADDR` (`allow_reuse_address = True`) is enforced to prevent socket collision (`[Errno 98] Address already in use`).
3. **Dedicated Shadow Relational & State Store**:
   - SQLite Ledger: `/opt/bybit-bot/reports/v32_dual_paper_preparation_001/shadow_execution_events.db`
   - State Snapshot: `/opt/bybit-bot/reports/v32_dual_paper_preparation_001/BOT_STATE_SHADOW_PERSISTENT.json`
   - Zero path intersection with the original v3.2 database.

---

## 2. Systemd Service Unit Specification

In strict compliance with Engineering Principles (Rule 6 in `AGENTS.md`):
- Optional environment files are prefixed with a hyphen: `EnvironmentFile=-/opt/bybit-bot/.env`.
- Fallback defaults are embedded directly inside the `[Service]` block.
- `PYTHONPATH` is explicitly declared.

```ini
[Unit]
Description=Bybit Multi-Alpha v3.2 Legacy Shadow Engine & Dashboard (Port 8084)
After=network.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/bybit-bot
EnvironmentFile=-/opt/bybit-bot/.env
Environment="PYTHONPATH=/opt/bybit-bot:/opt/bybit-bot/v3:/opt/bybit-bot/v3_2"
Environment="PYTHONUNBUFFERED=1"
Environment="PAPER_TRADING=true"
Environment="PORT=8084"
Environment="INITIAL_CAPITAL=300.000000"
Environment="ENABLE_AUTO_GEARBOX=true"
Environment="ENABLE_NEWS_DEFENSE=true"

ExecStart=/opt/bybit-bot/venv/bin/python -m v3_2.src.shadow.paper_trader_v32_shadow
Restart=always
RestartSec=5s
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

---

## 3. Directory & File Manifest

| Path | Purpose | Ownership / Mode |
| :--- | :--- | :--- |
| `/opt/bybit-bot/v3_2/src/shadow/paper_trader_v32_shadow.py` | Production Shadow Trading & Execution Engine | `root:root` `0644` |
| `/opt/bybit-bot/v3_2/src/shadow/web_dashboard_shadow.py` | Port 8084 Telemetry Web Server | `root:root` `0644` |
| `/opt/bybit-bot/reports/v32_dual_paper_preparation_001/` | Independent Shadow State & SQLite Directory | `root:root` `0755` |
| `/etc/systemd/system/bybit-v32-shadow.service` | Systemd service unit descriptor | `root:root` `0644` |

---

## 4. Pre-Flight Validation Checklist

Execute the following checklist prior to launching the Shadow service on the VPS:

- [ ] **1. Verify Original Port 8083 Integrity**:
  ```bash
  systemctl status bybit-v32.service --no-pager
  curl -s http://127.0.0.1:8083/api/status | grep "Bybit Multi-Alpha v3.2"
  ```
  *Requirement: Must return active (running) with untouched trade count.*

- [ ] **2. Verify Port 8084 Availability**:
  ```bash
  ss -tulpn | grep 8084
  ```
  *Requirement: Port 8084 must be completely vacant.*

- [ ] **3. Verify Independent Storage Directory**:
  ```bash
  ls -ld /opt/bybit-bot/reports/v32_dual_paper_preparation_001/
  ```
  *Requirement: Directory must exist and be writable.*

- [ ] **4. Unit Test Verification**:
  ```bash
  /opt/bybit-bot/venv/bin/python -m pytest tests/test_v32_parity_and_execution_proof.py
  ```
  *Requirement: 7/7 tests pass.*

- [ ] **5. Start & Verify Shadow Service**:
  ```bash
  systemctl daemon-reload
  systemctl enable bybit-v32-shadow.service
  systemctl start bybit-v32-shadow.service
  systemctl status bybit-v32-shadow.service --no-pager
  ```

- [ ] **6. Validate Telemetry API & Initial Capital**:
  ```bash
  curl -s http://127.0.0.1:8084/api/status | jq .
  ```
  *Requirement:*
  - `initial_capital`: `300.0`
  - `native_realized_equity`: `300.0`
  - `shadow_realized_equity`: `300.0`
  - `stream_connected`: `false` (when offline) / `true` (when WebSocket stream verified)
  - `native_gear`: `3`
  - `shadow_gear`: `3`
  - `crash_recovery_ready`: `true`
