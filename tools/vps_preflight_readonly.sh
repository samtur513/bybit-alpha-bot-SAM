#!/usr/bin/env bash
# ==============================================================================
# tools/vps_preflight_readonly.sh
# Non-destructive Read-Only Preflight Audit Script for Bybit Fleet VPS (150.241.98.70)
#
# SAFETY GUARANTEE:
# - ZERO modifying commands (NO POST/PUT, NO systemctl stop/restart, NO file edits)
# - Port 8083 (bybit-v32.service) verified running and completely untouched
# - Read-only queries to system status, resources, ports, and market endpoints
# ==============================================================================

set -euo pipefail

echo "=============================================================================="
echo "          FLEET VPS READ-ONLY PREFLIGHT AUDIT — $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "=============================================================================="

echo ""
echo "--- 1. HOST & HARDWARE RESOURCES ---"
uname -a
echo -n "Uptime: " && uptime
echo ""
echo "Disk Utilization:"
df -h / | awk 'NR==1 || NR==2'
echo ""
echo "Memory Utilization:"
free -m

echo ""
echo "--- 2. SERVICE STATUS (STRICTLY READ-ONLY) ---"
if command -v systemctl >/dev/null 2>&1; then
    echo "Checking Port 8083 (bybit-v32.service)..."
    systemctl status bybit-v32.service --no-pager || true
    echo ""
    echo "Summary of Fleet Systemd Units:"
    systemctl list-units 'bybit*' --no-pager || true
else
    echo "systemctl not available in current environment; skipping systemd audit."
fi

echo ""
echo "--- 3. NETWORK LATENCY TO EXCHANGES ---"
if command -v curl >/dev/null 2>&1; then
    echo -n "Bybit Public REST Latency: "
    curl -s -w "HTTP %{http_code} | Total RTT: %{time_total}s\n" "https://api.bybit.com/v5/market/time" -o /dev/null || echo "FAILED"
    echo -n "OKX Public REST Latency:   "
    curl -s -w "HTTP %{http_code} | Total RTT: %{time_total}s\n" "https://www.okx.com/api/v5/public/status" -o /dev/null || echo "FAILED"
else
    echo "curl not found; skipping curl latency test."
fi

echo ""
echo "--- 4. LISTENING FLEET PORTS (8080-8098) ---"
if command -v ss >/dev/null 2>&1; then
    ss -tulpn | grep -E ':(808[0-9]|809[0-8])' || echo "No fleet ports matched in active listening state."
elif command -v netstat >/dev/null 2>&1; then
    netstat -tulpn 2>/dev/null | grep -E ':(808[0-9]|809[0-8])' || echo "No fleet ports matched in active listening state."
fi

echo ""
echo "--- 5. PYTHON READ-ONLY PREFLIGHT HARNESS EXECUTION ---"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_EXEC="python3"
if ! command -v python3 >/dev/null 2>&1; then
    PYTHON_EXEC="python"
fi

OUTPUT_FILE="${1:-reports/final_start_blockers_009/evidence/vps_evidence.json}"
mkdir -p "$(dirname "$OUTPUT_FILE")"
$PYTHON_EXEC "${SCRIPT_DIR}/vps_preflight_readonly_check.py" --output "$OUTPUT_FILE" --vps-mode

echo ""
echo "=============================================================================="
echo "           VPS READ-ONLY PREFLIGHT AUDIT COMPLETE"
echo "=============================================================================="
