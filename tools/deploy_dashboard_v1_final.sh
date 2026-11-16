#!/usr/bin/env bash
# Deploy ONLY the READ-ONLY Dashboard (port 8085), pinned to an explicit commit.
# Usage: bash tools/deploy_dashboard_v1_final.sh <40-character-SHA>
set -Eeuo pipefail
REPO=/opt/bybit-shadow/bybit-alpha-bot-SAM
BRANCH=fix/dashboard-v1-chatgpt-20261116
PY=/opt/bybit-bot/venv/bin/python
SERVICE=bybit-shadow-dashboard.service
SHA="${1:-}"
FILES=(
  v3_2/src/integrations/three_system_dashboard.py
  v3_2/src/integrations/trading_control_center_data.py
  v3_2/src/runners/run_three_system_dashboard.py
)
STAGE=""
BACKUP=""
MODIFIED=0
RESTARTED=0

abort() { echo "BLOCKED: $*" >&2; on_error 1; }
cleanup() { [[ -z "$STAGE" || ! -d "$STAGE" ]] || rm -rf -- "$STAGE"; }
on_error() {
  local exitcode=${1:-$?}
  trap - ERR
  if [[ "$MODIFIED" == "1" ]]; then
    echo "INSTALL_FAILED: restoring previous Dashboard files from $BACKUP" >&2
    for file in "${FILES[@]}"; do
      if [[ -f "$BACKUP/$file" ]]; then
        install -m 0644 "$BACKUP/$file" "$REPO/$file" || true
      elif [[ -f "$BACKUP/$file.absent" ]]; then
        rm -f -- "$REPO/$file"
      fi
    done
    if [[ "$RESTARTED" == "1" ]]; then systemctl restart "$SERVICE" || true; fi
  fi
  echo "Rollback backup retained: $BACKUP" >&2
  exit "$exitcode"
}
trap cleanup EXIT
trap on_error ERR
[[ "$EUID" -eq 0 ]] || abort "root required"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || abort "supply exact 40-character commit SHA"
[[ -d "$REPO/.git" && -x "$PY" ]] || abort "repository or python unavailable"
systemctl is-active --quiet "$SERVICE" || abort "Dashboard service not active"

cd "$REPO"
echo "Current worktree HEAD: $(git rev-parse HEAD). No checkout/pull/reset/clean."
git fetch origin "$BRANCH"
git cat-file -e "${SHA}^{commit}"
git merge-base --is-ancestor "$SHA" "origin/$BRANCH" ||
  abort "commit is not on approved release branch"

STAGE=$(mktemp -d /opt/bybit-shadow/dashboard-stage.XXXXXXXX)
BACKUP=$(mktemp -d /opt/bybit-shadow/dashboard-backup.XXXXXXXX)
for file in "${FILES[@]}"; do
  mkdir -p "$STAGE/$(dirname "$file")" "$BACKUP/$(dirname "$file")"
  git show "${SHA}:$file" > "$STAGE/$file"
  expected=$(git rev-parse "${SHA}:$file")
  actual=$(git hash-object "$STAGE/$file")
  [[ "$expected" == "$actual" ]] || abort "staging checksum mismatch: $file"
  if [[ -f "$REPO/$file" ]]; then
    cp -a "$REPO/$file" "$BACKUP/$file"
    cmp -s "$REPO/$file" "$BACKUP/$file" || abort "backup mismatch: $file"
  else
    touch "$BACKUP/$file.absent"
  fi
done
echo "$SHA" > "$BACKUP/release.sha"
(
  cd "$STAGE"
  "$PY" -m py_compile "${FILES[@]}"
)
echo "STAGING_SYNTAX_PASS; backup=$BACKUP"

MODIFIED=1
for file in "${FILES[@]}"; do
  install -m 0644 "$STAGE/$file" "$REPO/$file"
  cmp -s "$STAGE/$file" "$REPO/$file"
done

# Dry run: no HTTP listener, only read-only existing ledger/telemetry sources.
PYTHONDONTWRITEBYTECODE=1 "$PY" "$REPO/v3_2/src/runners/run_three_system_dashboard.py" --dry-run

RESTARTED=1
systemctl restart "$SERVICE"
sleep 2
systemctl is-active --quiet "$SERVICE"
curl -fsS --max-time 8 http://127.0.0.1:8085/ -o "$STAGE/index.html"
grep -Fq "Trading Control Center" "$STAGE/index.html" || abort "HTML banner mismatch"

for endpoint in status scanner positions journal analytics health; do
  curl -fsS --max-time 8 "http://127.0.0.1:8085/api/$endpoint" -o "$STAGE/$endpoint.json"
  "$PY" -m json.tool "$STAGE/$endpoint.json" >/dev/null
done
"$PY" - "$STAGE/status.json" <<'PYCODE'
import json,sys
with open(sys.argv[1],encoding="utf-8") as file:
    status=json.load(file)
assert status["target_cohort"]["cohort_id"]=="20261116_000000_THREE_SYSTEM_SHADOW"
assert {"native","shadow","r2_shadow"} <= set(status["systems"])
print("Cohort and all 3 systems: PASS")
PYCODE
method=$(curl -sS --max-time 8 -X POST -o /dev/null -w '%{http_code}' http://127.0.0.1:8085/api/status)
[[ "$method" == 405 ]] || abort "READ_ONLY_METHOD_CHECK_FAILED"
trap - ERR
echo "DASHBOARD_DEPLOY_PASS: $SHA"
echo "Only $SERVICE restarted. Restore files available: $BACKUP"
