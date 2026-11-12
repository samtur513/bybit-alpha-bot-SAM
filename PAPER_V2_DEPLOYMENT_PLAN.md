# PAPER_V2_DEPLOYMENT_PLAN.md: VPS Staged Deployment Specification (Requires Approval)

**Audit Date**: November 12, 2026  
**Audit Protocol**: FLEET-REBOOT-001 (Step 3 Deployment Specification)  
**Evaluator**: Lead Quant Engineer & Independent Financial Auditor  
**Target Environment**: VPS Host `150.241.98.70` (Ubuntu Linux, systemd)  
**Git Branch**: `test/f1a-regression-suite`  
**Deployment Status**: **PENDING HUMAN CONFIRMATION (DO NOT EXECUTE WITHOUT APPROVAL)**  

---

## 1. Safety & Non-Interference Mandates

In accordance with General Constraints of FLEET-REBOOT-001:
1. **Zero VPS Disruption**: Existing services, processes, and paper databases (`paper_state.json`, `tca_attribution.db`) on VPS `150.241.98.70` **must not be stopped, deleted, or reset**.
2. **Dedicated Isolated Databases**: Paper V2 runs on dedicated SQLite ledger files named `paper_v2_verified.db` with separate table namespaces.
3. **Legacy Data Archiving**: Existing telemetry is preserved under `LEGACY_PAPER`. All new telemetry is tagged `PAPER_V2_VERIFIED`.
4. **No Real Orders**: Execution mode remains strictly `LIVE_PAPER_SIMULATED`.

---

## 2. Target Scope for Paper V2 Phase 1 (Top 3 Priority Bots)

| Target Port | Strategy Module | VPS Directory | New Service Name | Dedicated Ledger DB | Allocated Virtual Capital |
| :---: | :--- | :--- | :--- | :--- | :---: |
| **8096** | v3.12 Cross-Basis Arbitrage | `/opt/bybit-bot/v3_12_crossbasis` | `bybit-crossbasis-8096.service` | `crossbasis_v2.db` | $300.00 USDT |
| **8091** | v3.8 NewDragon Trend Lab | `/opt/bybit-bot/v3_8` | `bybit-bot-v38.service` | `trend_lab_v38_v2.db` | $300.00 USDT |
| **8084** | v3.1.4 Champion 4h | `/opt/bybit-bot/v3_1_4` | `bybit-v314.service` | `champion_v314_v2.db` | $300.00 USDT |

---

## 3. Step-by-Step Staged Deployment Procedure (Post-Approval)

When the operator grants authorization to deploy, the deployment must execute through the following exact steps:

### Step 1: Pre-Deployment Backup
```bash
# On VPS: Backup existing working directories without stopping services
mkdir -p /opt/bybit-bot/backups/legacy_paper_$(date +%Y%m%d_%H%M%S)
cp -r /opt/bybit-bot/v3_1_4/*.json /opt/bybit-bot/backups/legacy_paper_*/ 2>/dev/null || true
cp -r /opt/bybit-bot/v3_8/*.json /opt/bybit-bot/backups/legacy_paper_*/ 2>/dev/null || true
cp -r /opt/bybit-bot/v3_12_crossbasis/*.json /opt/bybit-bot/backups/legacy_paper_*/ 2>/dev/null || true
```

### Step 2: Fetch Verified Codebase
```bash
cd /opt/bybit-bot
git fetch origin test/f1a-regression-suite
git checkout 9332f2cf2b15e9d771b18cbe278888ae954b385a
```

### Step 3: Install Universal Execution Contract & Ledger
```bash
# Verify that tools/fleet_execution_contract.py is available in PYTHONPATH
export PYTHONPATH="/opt/bybit-bot:/opt/bybit-bot/v3_1_4:/opt/bybit-bot/v3_8:/opt/bybit-bot/v3_12_crossbasis"
python -m pytest tests/test_fleet_execution_contract.py
```

### Step 4: Configuration of Verified Environment Files
In each target module directory, create an isolated `.env.paper_v2`:
```bash
# /opt/bybit-bot/v3_1_4/.env.paper_v2
PAPER_TRADING=true
USE_MNT_DISCOUNT=false
BASE_TAKER_FEE=0.00100
BASE_MAKER_FEE=0.00036
LEDGER_DB_PATH=/opt/bybit-bot/v3_1_4/champion_v314_v2.db
RUN_ID=PAPER_V2_VERIFIED_2026_11
```

### Step 5: Rolling Systemd Service Restart (With SO_REUSEADDR)
```bash
# Restart target services one by one with 5s verification intervals
sudo systemctl restart bybit-crossbasis-8096.service
sleep 5
curl -s http://localhost:8096/api/status | jq .

sudo systemctl restart bybit-bot-v38.service
sleep 5
curl -s http://localhost:8091/api/status | jq .

sudo systemctl restart bybit-v314.service
sleep 5
curl -s http://localhost:8084/api/status | jq .
```

### Step 6: Post-Deployment Telemetry Verification
```bash
# Verify all 3 services are online and reporting to Meta-Dashboard (8085)
curl -s http://localhost:8085/api/meta_status | jq '{online_engines: .online_engines, combined_equity: .total_combined_equity}'
```

---

## 4. Rollback Plan

If any service encounters unexpected crashes or unhandled exceptions within the first 60 minutes:
1. Revert Git HEAD:
   ```bash
   git checkout HEAD~1
   ```
2. Restore backup JSON states:
   ```bash
   cp /opt/bybit-bot/backups/legacy_paper_*/* /opt/bybit-bot/
   ```
3. Restart original systemd units:
   ```bash
   sudo systemctl restart bybit-crossbasis-8096 bybit-bot-v38 bybit-v314
   ```
4. Confirm telemetry restoration on Port 8085.

---

## 5. Explicit Approval Checkpoint

**ACTION REQUIRED BY HUMAN OPERATOR**:  
Review the above deployment plan and explicitly confirm authorization before any commands are executed on VPS `150.241.98.70`.
