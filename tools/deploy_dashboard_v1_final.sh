#!/usr/bin/env bash
# tools/deploy_dashboard_v1_final.sh
# Production-Safe Deployment & Automated Rollback Script for TORG_BOT Trading Control Center v1.0
# Target Service: bybit-shadow-dashboard.service (Port 8085)
# Invariant: NEVER touch Native, Shadow, R2, or v3.2 production engines.

set -euo pipefail

REPO_DIR="/opt/bybit-shadow/bybit-alpha-bot-SAM"
V32_SRC="$REPO_DIR/v3_2/src/integrations"
BACKUP_BASE_DIR="/opt/bybit-shadow/backup_dashboard"
TIMESTAMP=$(date -u +"%Y%m%dT%H%M%SZ")
BACKUP_DIR="$BACKUP_BASE_DIR/backup_$TIMESTAMP"
TARGET_PORT=8085
HEALTH_URL="http://127.0.0.1:${TARGET_PORT}/health"
HTML_URL="http://127.0.0.1:${TARGET_PORT}/"
PYTHON_BIN="/opt/bybit-bot/venv/bin/python"

echo "================================================================="
echo "  TORG_BOT TRADING CONTROL CENTER v1.0 — DEPLOYMENT & VERIFICATION"
echo "  Timestamp: $TIMESTAMP"
echo "================================================================="

# Step 1: Verify current directory and Git status
cd "$REPO_DIR"
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
CURRENT_COMMIT=$(git rev-parse HEAD)
echo "[1] Current Branch: $CURRENT_BRANCH | Current Commit: $CURRENT_COMMIT"

# Step 2: Syntax and Import Verification in Staging
echo "[2] Verifying Python syntax of updated integration modules..."
$PYTHON_BIN -m py_compile "$V32_SRC/trading_control_center_data.py"
$PYTHON_BIN -m py_compile "$V32_SRC/three_system_dashboard.py"
echo "    -> Syntax check: PASS"

# Step 3: Create atomic backup of existing working files
echo "[3] Creating backup of current dashboard implementation..."
mkdir -p "$BACKUP_DIR"
cp "$V32_SRC/trading_control_center_data.py" "$BACKUP_DIR/trading_control_center_data.py.bak"
cp "$V32_SRC/three_system_dashboard.py" "$BACKUP_DIR/three_system_dashboard.py.bak"

sha256sum "$BACKUP_DIR/trading_control_center_data.py.bak" > "$BACKUP_DIR/checksums.sha256"
sha256sum "$BACKUP_DIR/three_system_dashboard.py.bak" >> "$BACKUP_DIR/checksums.sha256"
echo "    -> Backup created at: $BACKUP_DIR"
cat "$BACKUP_DIR/checksums.sha256"

# Rollback function in case of failure
rollback() {
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    echo "  DEPLOYMENT FAILED! INITIATING AUTOMATED ROLLBACK..."
    echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
    cp "$BACKUP_DIR/trading_control_center_data.py.bak" "$V32_SRC/trading_control_center_data.py"
    cp "$BACKUP_DIR/three_system_dashboard.py.bak" "$V32_SRC/three_system_dashboard.py"
    echo "    -> Files restored from backup."
    sudo systemctl restart bybit-shadow-dashboard.service
    echo "    -> Service bybit-shadow-dashboard.service restarted with previous code."
    sleep 3
    if curl -s -f -m 5 "$HTML_URL" > /dev/null; then
        echo "    -> Rollback verified successfully: Dashboard is healthy."
    else
        echo "    -> CRITICAL: Manual intervention required!"
    fi
    exit 1
}

trap rollback ERR

# Step 4: Verify and load new changes
echo "[4] Pulling or verifying Git commit..."
# (If running as deployer, git pull origin fix/final-start-blockers-009 has already updated files)
DEPLOYED_COMMIT=$(git rev-parse HEAD)
echo "    -> Deployed Commit: $DEPLOYED_COMMIT"

# Step 5: Restart ONLY the dashboard service
echo "[5] Restarting bybit-shadow-dashboard.service..."
sudo systemctl restart bybit-shadow-dashboard.service
echo "    -> Restart signal sent. Waiting 3 seconds for port ${TARGET_PORT}..."
sleep 3

# Step 6: Post-deployment health & HTML verification
echo "[6] Performing automated health checks on port ${TARGET_PORT}..."
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" -m 5 "$HTML_URL" || echo "000")
if [ "$HTTP_STATUS" != "200" ]; then
    echo "[-] Health check failed! HTTP status: $HTTP_STATUS"
    rollback
fi

# Check HTML content contains Trading Control Center and no corruption
HTML_CONTENT=$(curl -s -m 5 "$HTML_URL")
if echo "$HTML_CONTENT" | grep -q "TORG_BOT — Trading Control Center"; then
    echo "    -> HTTP 200 OK & 'TORG_BOT — Trading Control Center' banner verified."
else
    echo "[-] Banner check failed!"
    rollback
fi

# Step 7: Final Status Output
trap - ERR
echo "================================================================="
echo "  DEPLOYMENT SUCCESSFUL!"
echo "  Target Port: ${TARGET_PORT}"
echo "  Commit: $DEPLOYED_COMMIT"
echo "  Backup: $BACKUP_DIR"
echo "================================================================="
exit 0
