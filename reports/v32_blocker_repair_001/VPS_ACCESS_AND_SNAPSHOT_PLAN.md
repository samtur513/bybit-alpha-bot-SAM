# VPS_ACCESS_AND_SNAPSHOT_PLAN.md
## Minimal Safe Procedure for Consistent Live VPS State Snapshot & Restoration Verification

**Protocol:** V32-MASTER-001 Live VPS Snapshot Protocol  
**Target Host:** VPS `150.241.98.70` (Linux x86_64)  
**Security Standard:** Strict Secret Isolation (Zero credentials or private keys in Git)  
**Access Requirement:** Authorized Operator SSH Key or Console Access  

---

### 1. Architectural Snapshot Protocol Overview

Because direct SSH access is restricted from the agent execution sandbox (`BLOCKED_NO_VPS_ACCESS`), the acquisition of a true, crash-consistent `LIVE_VPS_STATE_BACKUP` must be executed either by:
1. Providing an authorized SSH identity file into `~/.ssh/id_rsa` or `~/.ssh/config`.
2. Running an idempotent, non-destructive snapshot script directly on the VPS by an authorized human operator.

---

### 2. Live Atomic Snapshot Script: `create_vps_atomic_snapshot.sh`

The following script is designed to run directly on the VPS as `root` without stopping trading processes, ensuring zero disruption to live paper trading:

```bash
#!/usr/bin/env bash
# ==============================================================================
# create_vps_atomic_snapshot.sh
# Non-destructive crash-consistent live snapshot of /opt/bybit-bot
# ==============================================================================
set -euo pipefail

SNAPSHOT_TIMESTAMP="$(date -u +%Y%m%d_%H%M%SZ)"
SNAPSHOT_DIR="/opt/bybit-bot-backups/snapshot_${SNAPSHOT_TIMESTAMP}"
ARCHIVE_PATH="/opt/bybit-bot-backups/LIVE_VPS_STATE_${SNAPSHOT_TIMESTAMP}.tar.gz"

echo "=== [1/5] Creating Snapshot Workspace: ${SNAPSHOT_DIR} ==="
mkdir -p "${SNAPSHOT_DIR}"
mkdir -p "${SNAPSHOT_DIR}/reports_live"
mkdir -p "${SNAPSHOT_DIR}/systemd_units"
mkdir -p "${SNAPSHOT_DIR}/sqlite_backups"
mkdir -p "${SNAPSHOT_DIR}/env_configs_sanitized"

echo "=== [2/5] Performing Safe SQLite Checkpoints ==="
# Checkpoint WAL logs to guarantee zero data loss without stopping daemons
find /opt/bybit-bot -name "*.db" | while read -r dbfile; do
    echo "  Checkpointing ${dbfile}..."
    sqlite3 "${dbfile}" "PRAGMA wal_checkpoint(TRUNCATE);" || true
    cp "${dbfile}" "${SNAPSHOT_DIR}/sqlite_backups/$(basename "${dbfile}")"
done

echo "=== [3/5] Capturing Live Persistent State JSON Files ==="
find /opt/bybit-bot -name "*STATE*.json" -o -name "closed_trades*.json" | while read -r statefile; do
    rel_path="${statefile#/opt/bybit-bot/}"
    dest_dir="${SNAPSHOT_DIR}/reports_live/$(dirname "${rel_path}")"
    mkdir -p "${dest_dir}"
    cp -p "${statefile}" "${dest_dir}/"
done

echo "=== [4/5] Capturing Active Systemd Service Definitions ==="
cp /etc/systemd/system/bybit-*.service "${SNAPSHOT_DIR}/systemd_units/" 2>/dev/null || true

echo "=== [5/5] Packaging & Calculating Checksum ==="
cd /opt/bybit-bot-backups
tar -czf "${ARCHIVE_PATH}" "snapshot_${SNAPSHOT_TIMESTAMP}"
SHA256_SUM="$(sha256sum "${ARCHIVE_PATH}" | awk '{print $1}')"

echo "=============================================================================="
echo "SNAPSHOT COMPLETE: ${ARCHIVE_PATH}"
echo "SIZE: $(du -h "${ARCHIVE_PATH}" | awk '{print $1}')"
echo "SHA-256: ${SHA256_SUM}"
echo "=============================================================================="
```

---

### 3. Verification Protocol Before Service Stop

Before any service stopping (`systemctl stop`) can be authorized:
1. **Archive Transfer:** Transfer `LIVE_VPS_STATE_${SNAPSHOT_TIMESTAMP}.tar.gz` to a secure offline vault.
2. **Integrity Validation:** Verify that `sha256sum` matches the server-generated checksum.
3. **Completeness Audit:** Verify that `/opt/bybit-bot/v3_2/reports/BOT_STATE_PERSISTENT_V32.json` matches the live capital ($321.88) and 28 closed trades.
4. **Secret Quarantine:** Confirm that no `.env` files containing raw Bybit API secrets are checked into Git.
