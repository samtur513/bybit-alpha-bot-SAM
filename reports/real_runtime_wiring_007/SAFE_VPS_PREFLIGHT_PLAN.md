# SAFE_VPS_PREFLIGHT_PLAN.md
## Protocol: TORG-BOT — REAL-RUNTIME-WIRING-007
### Hardened Operational Plan & Safe VPS Deployment Architecture

**Target VPS**: `150.241.98.70`  
**Current Production Service (Port 8083)**: `bybit-v32.service` in `/opt/bybit-bot/v3_2`  
**Execution Status**: `OPERATOR_APPROVAL_REQUIRED / ZERO_VPS_MUTATION_PERMITTED`  
**Candidate T0 Schedule**: `2026-11-17T00:00:00Z` (Status: `schedule_active = False`)

---

## 1. Absolute Operational Prohibitions (Safety Rules)

To eliminate operational risk and prevent corruption of the live trading system on Port 8083, the following hard constraints are strictly enforced:

1. **FORBID `cp -a` on Live SQLite Databases**:
   - `cp` on active SQLite databases with WAL files (`-wal`, `-shm`) causes silent file corruption or torn pages.
   - **Mandatory Safe Alternative**:
     ```bash
     sqlite3 /opt/bybit-bot/v3_2/v32_paper_ledger.db ".backup '/opt/backups_safe/v32_paper_ledger_backup.db'"
     ```
     or using SQLite `VACUUM INTO`:
     ```bash
     sqlite3 /opt/bybit-bot/v3_2/v32_paper_ledger.db "VACUUM INTO '/opt/backups_safe/v32_paper_ledger_vacuum.db';"
     ```

2. **FORBID `chown -R` on Production Directories**:
   - Recursive ownership mutations risk breaking permissions of existing systemd daemons.
   - All operations must target only newly created, isolated paths owned by the unprivileged user.

3. **FORBID Running Daemons as `root`**:
   - All services must run under the designated unprivileged system user (`bybit` or `botuser`).
   - Systemd units must explicitly define `User=bybit` and `Group=bybit`.

4. **FORBID Listening on `0.0.0.0`**:
   - Dashboard (Port 8099) must bind strictly to `127.0.0.1:8099`.
   - External access is permitted exclusively via authenticated SSH tunnels:
     ```bash
     ssh -L 8099:127.0.0.1:8099 user@150.241.98.70
     ```

5. **FORBID Trading API Keys / Private Secrets**:
   - Paper trading engines must operate strictly in Read-Only mode against public market data endpoints.
   - Zero private API keys, demo keys, or account signing credentials shall be placed in environment files.

6. **FORBID Modifying or Switching Branches in `/opt/bybit-bot/v3_2`**:
   - The production directory `/opt/bybit-bot/v3_2` hosting Port 8083 is FROZEN and UNTOUCHABLE.
   - New Shadow and Native runners must be installed into an isolated dedicated directory: `/opt/bybit-shadow/`.

7. **FORBID Immediate Start Before Operator Sign-off**:
   - `schedule_active` remains `False`. No prospective live study begins without explicit human operator instruction.

---

## 2. Target Fleet Architecture & Port Allocations

```
VPS 150.241.98.70
│
├── [EXISTING / UNTOUCHED]
│   └── Port 8083: bybit-v32.service (Original Production Donchian Trend, Dir: /opt/bybit-bot/v3_2)
│       └── Database: /opt/bybit-bot/v3_2/v32_paper_ledger.db
│
└── [NEW ISOLATED RESEARCH DEPLOYMENT (Dir: /opt/bybit-shadow/)]
    │
    ├── System A (v3.2 Native Paper Runner):
    │   ├── Runtime: run_native_paper.py
    │   ├── Starting Capital: 300.000000 USDT (Isolated)
    │   ├── Database: /opt/bybit-shadow/data/v32_native_ledger.db
    │   └── Telemetry: /opt/bybit-shadow/data/telemetry_native.json
    │
    ├── System B (v3.2 Realistic Shadow Paper Runner):
    │   ├── Runtime: run_shadow_paper.py (Discrete L2 depth walking + spread gate)
    │   ├── Starting Capital: 300.000000 USDT (Isolated)
    │   ├── Database: /opt/bybit-shadow/data/v32_shadow_ledger.db
    │   └── Telemetry: /opt/bybit-shadow/data/telemetry_shadow.json
    │
    ├── System C (CryptoLab R2-SHADOW Consumer):
    │   ├── Runtime: Read-only observer polling
    │   ├── Telemetry Path: /opt/bybit-shadow/cryptolab/telemetry.json
    │   └── Starting Capital: 300.000000 USDT (Spot Cash, 24 Profiles)
    │
    └── Unified Dashboard Daemon:
        ├── Runtime: run_three_system_dashboard.py
        ├── Binding: 127.0.0.1:8099 (Safe Localhost Only)
        └── Systemd Unit: bybit-three-sys-dash.service
```

---

## 3. Step-by-Step Operator Preflight Checklist

When authorized by operator, deployment shall proceed according to this non-destructive checklist:

### Phase 1: Pre-Flight Integrity Verification (Read-Only)
- [ ] Check status of live Port 8083:
  ```bash
  systemctl status bybit-v32.service
  curl -s http://127.0.0.1:8083/health
  ```
- [ ] Verify safe online SQLite backup of historical Port 8083 database:
  ```bash
  mkdir -p /opt/backups_safe
  sqlite3 /opt/bybit-bot/v3_2/v32_paper_ledger.db ".backup '/opt/backups_safe/v32_paper_ledger_pre007.db'"
  sqlite3 /opt/backups_safe/v32_paper_ledger_pre007.db "PRAGMA integrity_check;"
  ```

### Phase 2: Create Isolated Directory & Permissions
- [ ] Create dedicated directory `/opt/bybit-shadow/`:
  ```bash
  mkdir -p /opt/bybit-shadow/data
  mkdir -p /opt/bybit-shadow/logs
  ```
- [ ] Clone or deploy `feature/real-runtime-wiring-007` exclusively into `/opt/bybit-shadow/`:
  ```bash
  git clone -b feature/real-runtime-wiring-007 https://github.com/samtur513/bybit-alpha-bot-SAM.git /opt/bybit-shadow/repo
  ```

### Phase 3: Systemd Unit Definitions (Hardened)

#### 1. Native Paper Runner Unit (`/etc/systemd/system/bybit-v32-native-paper.service`)
```ini
[Unit]
Description=TORG-BOT v3.2 Native Paper Trading Runner (New Cohort)
After=network.target

[Service]
Type=simple
User=bybit
Group=bybit
WorkingDirectory=/opt/bybit-shadow/repo
Environment="PYTHONPATH=/opt/bybit-shadow/repo"
Environment="PYTHONUNBUFFERED=1"
ExecStart=/opt/bybit-shadow/venv/bin/python v3_2/src/runners/run_native_paper.py \
    --db-path /opt/bybit-shadow/data/v32_native_ledger.db \
    --telemetry-file /opt/bybit-shadow/data/telemetry_native.json
Restart=always
RestartSec=10
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

#### 2. Realistic Shadow Runner Unit (`/etc/systemd/system/bybit-v32-shadow-paper.service`)
```ini
[Unit]
Description=TORG-BOT v3.2 Realistic Shadow Paper Runner (L2 Depth & Spread Gate)
After=network.target

[Service]
Type=simple
User=bybit
Group=bybit
WorkingDirectory=/opt/bybit-shadow/repo
Environment="PYTHONPATH=/opt/bybit-shadow/repo"
Environment="PYTHONUNBUFFERED=1"
ExecStart=/opt/bybit-shadow/venv/bin/python v3_2/src/runners/run_shadow_paper.py \
    --db-path /opt/bybit-shadow/data/v32_shadow_ledger.db \
    --telemetry-file /opt/bybit-shadow/data/telemetry_shadow.json
Restart=always
RestartSec=10
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
```

#### 3. Three-System Comparative Dashboard Unit (`/etc/systemd/system/bybit-three-sys-dash.service`)
```ini
[Unit]
Description=TORG-BOT Three-System Comparative Dashboard Daemon (Port 8099)
After=network.target

[Service]
Type=simple
User=bybit
Group=bybit
WorkingDirectory=/opt/bybit-shadow/repo
Environment="PYTHONPATH=/opt/bybit-shadow/repo"
Environment="PYTHONUNBUFFERED=1"
ExecStart=/opt/bybit-shadow/venv/bin/python v3_2/src/runners/run_three_system_dashboard.py \
    --host 127.0.0.1 \
    --port 8099 \
    --native-telemetry /opt/bybit-shadow/data/telemetry_native.json \
    --shadow-telemetry /opt/bybit-shadow/data/telemetry_shadow.json \
    --r2-telemetry /opt/bybit-shadow/cryptolab/telemetry.json
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

### Phase 4: Post-Deployment Smoke Test
- [ ] Verify Port 8099 responds locally:
  ```bash
  curl -s http://127.0.0.1:8099/health | jq .
  curl -s http://127.0.0.1:8099/api/status | jq .
  ```
- [ ] Confirm Port 8083 remains 100% healthy and undisturbed:
  ```bash
  curl -s http://127.0.0.1:8083/health | jq .
  ```
- [ ] Connect SSH tunnel on operator workstation:
  ```powershell
  ssh -N -L 8099:127.0.0.1:8099 bybit@150.241.98.70
  ```
  Open `http://localhost:8099` in web browser.
