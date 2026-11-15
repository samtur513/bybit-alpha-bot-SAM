# VPS PREFLIGHT HANDOFF & READINESS DOSSIER
## TORG-BOT — FORWARD-LIVE-DATA-GATE-008
**Protocol ID**: `TORG-BOT — FORWARD-LIVE-DATA-GATE-008`  
**Repository**: `samtur513/bybit-alpha-bot-SAM`  
**Base Commit**: `04acbd2c28cfaa1320bbc50454d23c49454300ec`  
**Branch**: `feature/forward-live-data-gate-008`  
**Target VPS Host**: `150.241.98.70` (User: `root`)  
**Handoff Date**: 2026-11-15  
**Final Status Declaration**: `FORWARD_RUNTIME_OFFLINE_VERIFIED / VPS_PREFLIGHT_PENDING`

---

## 1. Absolute Operational Safety Constraints

> [!CAUTION]
> **STRICT VPS PRESERVATION INVARIANT**:
> 1. **Zero Remote Modification During Gate-008**: All work performed under `GATE-008` is 100% local and offline. Zero SSH commands have been executed against `150.241.98.70`.
> 2. **Port 8083 Untouched**: The original historical v3.2 service (`bybit-v32.service`) operating on port 8083 has NOT been stopped, restarted, reconfigured, or modified in any manner. It remains running in its historical configuration.
> 3. **Zero Real/Demo Exchange Orders**: No orders have been or will be submitted to Bybit trading endpoints. All execution verification is conducted via local mathematical orderbook walking and paper state ledgers.
> 4. **No Service Disruptions**: No other services in the fleet (ports 8080..8098) are touched.

---

## 2. Cohort Synchronization & Candidate T0 Specification

### 2.1 Candidate T0 Timestamp
- **Candidate T0 (UTC)**: `2026-11-17T00:00:00+00:00`
- **Canonical Cohort Identifier**: `TORG_THREE_SYS_20261115_001`
- **Initial Study Capital per System**: `300.000000 USDT`
- **Active Schedule Flag**: **`schedule_active = False`**

### 2.2 Pre-T0 Invariants
Until explicit human authorization is granted to activate the forward study schedule (`schedule_active = True`):
1. **Zero Study Trades Before T0**: Any trades or warm-up executions occurring prior to T0 are categorized as technical calibration and MUST NOT be counted towards forward performance attribution.
2. **Warm-Up Isolation**: Both Native and Shadow runners synchronize the 120-bar Donchian channel history without opening active study positions.
3. **Telemetry Truthfulness**: If telemetry is rendered prior to activation or if market data is absent, the dashboard strictly flags status as `NOT_STARTED` or `OBSERVING`, displaying `UNVALUED_OR_STALE` with `--` for unconfirmed equity.

---

## 3. Fleet Systemd Service Architecture & Allocations

To guarantee absolute isolation between the historical original v3.2 on port 8083 and the new 3-system forward paper study, the following service layout is prepared for eventual deployment:

```
VPS 150.241.98.70
├── Port 8083: bybit-v32.service (Original Historical v3.2 - UNTOUCHED & ACTIVE)
│
├── System A (Native v3.2 Forward):
│   ├── Service: bybit-v32-native-paper.service
│   ├── Working Directory: /opt/bybit-bot/v3_2_native_paper
│   ├── State: reports/v32_native_state.json
│   └── Ledger: reports/v32_native_ledger.db
│
├── System B (v3.2 Legacy Shadow Forward):
│   ├── Service: bybit-v32-shadow-paper.service
│   ├── Working Directory: /opt/bybit-bot/v3_2_shadow_paper
│   ├── State: reports/v32_shadow_state.json
│   └── Ledger: reports/v32_shadow_ledger.db
│
├── System C (CryptoLab R2-Shadow Forward):
│   ├── Observer Working Directory: /opt/cryptolab/r2_shadow
│   ├── Telemetry Export: /opt/cryptolab/r2_shadow/telemetry.json
│   └── Verification: runtime.verify_frozen() hash pass
│
└── Multi-System Dashboard:
    ├── Service: bybit-three-sys-dashboard.service
    ├── Working Directory: /opt/bybit-bot/three_system_dashboard
    └── Bind: Port 8085 (or dedicated port)
```

### 3.1 Systemd Unit Specifications (Reference Templates)

#### A. System A: `bybit-v32-native-paper.service`
```ini
[Unit]
Description=TORG-BOT Native v3.2 Paper Runner (Gate-008)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/bybit-bot/v3_2_native_paper
Environment="PYTHONPATH=/opt/bybit-bot"
Environment="PYTHONUNBUFFERED=1"
EnvironmentFile=-/opt/bybit-bot/.env
ExecStart=/opt/bybit-bot/venv/bin/python v3_2/src/runners/run_native_paper.py --db reports/v32_native_ledger.db --state reports/v32_native_state.json --telemetry reports/telemetry_native.json --cohort TORG_THREE_SYS_20261115_001 --t0 2026-11-17T00:00:00+00:00
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### B. System B: `bybit-v32-shadow-paper.service`
```ini
[Unit]
Description=TORG-BOT Legacy Shadow Paper Runner (Gate-008)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/bybit-bot/v3_2_shadow_paper
Environment="PYTHONPATH=/opt/bybit-bot"
Environment="PYTHONUNBUFFERED=1"
EnvironmentFile=-/opt/bybit-bot/.env
ExecStart=/opt/bybit-bot/venv/bin/python v3_2/src/runners/run_shadow_paper.py --db reports/v32_shadow_ledger.db --state reports/v32_shadow_state.json --telemetry reports/telemetry_shadow.json --cohort TORG_THREE_SYS_20261115_001 --t0 2026-11-17T00:00:00+00:00
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

## 4. Operator Preflight Verification Checklist

Prior to launching services on VPS `150.241.98.70`, the human operator must execute and verify the following sequence:

- [ ] **Step 1: Code Synchronization**: Pull verified branch `feature/forward-live-data-gate-008` (commit matching manifest).
- [ ] **Step 2: Regression Pass**: Execute offline test suite on target python environment:
  ```bash
  python -m pytest tests/test_forward_live_data_gate_008.py -v
  ```
  Ensure all 6 tests pass without error.
- [ ] **Step 3: Port 8083 Verification**: Inspect status of running service:
  ```bash
  systemctl status bybit-v32.service
  curl -s http://127.0.0.1:8083/api/status | jq .
  ```
  Confirm it is active and untouched.
- [ ] **Step 4: CryptoLab Hash Verification**: Confirm 9/9 files pass SHA256 integrity:
  ```bash
  python -c "import runtime; manifest = runtime.verify_frozen(); print('Integrity:', len(manifest['files']), 'files OK')"
  ```
- [ ] **Step 5: Authorization of T0**: When ready to begin the comparative forward study, human operator switches `schedule_active = True`.

---

## 5. Certification of Completion

The technical prerequisites for continuous forward paper trading under `TORG-BOT — FORWARD-LIVE-DATA-GATE-008` have been fully implemented, transactionally hardened, and tested offline.

**Current Verified State**: `FORWARD_RUNTIME_OFFLINE_VERIFIED`  
**Deployment State**: `VPS_PREFLIGHT_PENDING` (Awaiting Human Operator Approval)
