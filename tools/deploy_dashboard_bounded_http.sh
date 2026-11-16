#!/usr/bin/env bash
# Narrow recovery for the stalled single-threaded TORG_BOT HTTP on port 8085.
# Usage: bash deploy_dashboard_bounded_http.sh <exact-40-char-sha> --check|--apply
# Only the Dashboard source and systemd unit are affected; never modify trading ledgers.
set -Eeuo pipefail

REPO=/opt/bybit-shadow/bybit-alpha-bot-SAM
REL=v3_2/src/integrations/three_system_dashboard.py
FILE="$REPO/$REL"
SVC=bybit-shadow-dashboard.service
BRANCH=fix/dashboard-bounded-http-20261116
PY=/opt/bybit-bot/venv/bin/python
OLD_GIT_BLOB=5442b84b9f1bde11ba42e2bcb9b41a156c7e2729
SHA="$1"
MODE="$2"
STAGE=""
BACKUP=""
STOPPED=0
SUCCESS=0

cleanup() {
  local rc=$?
  trap - EXIT
  if [[ "$STOPPED" == 1 && "$SUCCESS" == 0 && -f "$BACKUP/three_system_dashboard.py" ]]; then
    echo "ROLLBACK: restoring Dashboard source from $BACKUP" >&2
    systemctl stop "$SVC" || true
    cp -a "$BACKUP/three_system_dashboard.py" "$FILE"
    systemctl start "$SVC" || true
    echo "ROLLBACK_ATTEMPTED; check service logs" >&2
  fi
  if [[ -n "$STAGE" && -d "$STAGE" ]]; then rm -rf "$STAGE"; fi
  exit "$rc"
}
trap cleanup EXIT
die() { echo "BLOCKED: $*" >&2; exit 1; }

[[ "$EUID" == 0 ]] || die "root required"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || die "exact 40-character SHA required"
[[ "$MODE" == --check || "$MODE" == --apply ]] || die "expected --check or --apply"
[[ -d "$REPO/.git" && -f "$FILE" && -x "$PY" ]] || die "unexpected VPS layout"
[[ "$(systemctl show "$SVC" -p LoadState --value)" == loaded ]] || die "dashboard unit unavailable"
EXEC=$(systemctl show "$SVC" -p ExecStart --value)
[[ "$EXEC" == *run_three_system_dashboard* ]] || die "unexpected Dashboard ExecStart"
[[ "$(git -C "$REPO" hash-object "$FILE")" == "$OLD_GIT_BLOB" ]] ||
  die "live Dashboard source differs from approved baseline; refuse overwrite"

cd "$REPO"
git fetch origin "$BRANCH"
git cat-file -e "$SHA^{commit}" || die "commit not fetched"
git merge-base --is-ancestor "$SHA" "origin/$BRANCH" ||
  die "commit not in authorized branch"

STAGE=$(mktemp -d /opt/bybit-shadow/dashboard-http-stage.XXXXXXXX)
git show "$SHA:$REL" > "$STAGE/three_system_dashboard.py"
[[ -s "$STAGE/three_system_dashboard.py" ]] || die "missing staged source"
[[ "$(git hash-object "$STAGE/three_system_dashboard.py")" == "$(git rev-parse "$SHA:$REL")" ]] ||
  die "staged source hash mismatch"
"$PY" -B -m py_compile "$STAGE/three_system_dashboard.py"
grep -q 'class BoundedDashboardHTTPServer(ThreadingHTTPServer)' "$STAGE/three_system_dashboard.py" ||
  die "bounded concurrency server missing"
echo "DASHBOARD_HTTP_CHECK_PASS: exact baseline, staged source, independent dashboard unit"

if [[ "$MODE" == --check ]]; then
  SUCCESS=1
  exit 0
fi

BACKUP=$(mktemp -d /opt/bybit-shadow/dashboard-http-backup.XXXXXXXX)
cp -a "$FILE" "$BACKUP/three_system_dashboard.py"
cmp -s "$FILE" "$BACKUP/three_system_dashboard.py" || die "backup verification failed"
printf '%s\n' "$SHA" > "$BACKUP/approved_sha.txt"
echo "DASHBOARD_HTTP_BACKUP_VERIFIED: $BACKUP"

STOPPED=1
timeout 20s systemctl stop "$SVC"
! systemctl is-active --quiet "$SVC" || die "dashboard failed to stop"
install -m 0644 "$STAGE/three_system_dashboard.py" "$FILE"
cmp -s "$STAGE/three_system_dashboard.py" "$FILE" || die "live source copy mismatch"
timeout 20s systemctl start "$SVC"
systemctl is-active --quiet "$SVC" || die "dashboard not active after start"

HEALTH=000
for attempt in {1..8}; do
  HEALTH=$(curl -sS --connect-timeout 2 --max-time 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8085/health 2>/dev/null || true)
  [[ "$HEALTH" == 200 ]] && break
  sleep 1
done
[[ "$HEALTH" == 200 ]] || die "dashboard /health did not return HTTP 200"
ROOT_CODE=$(curl -sS --connect-timeout 3 --max-time 15 -o /dev/null -w '%{http_code}' http://127.0.0.1:8085/ 2>/dev/null || true)
[[ "$ROOT_CODE" == 200 ]] || die "dashboard homepage failed post-install, HTTP=$ROOT_CODE"
SUCCESS=1
STOPPED=0
echo "DASHBOARD_BOUNDED_HTTP_PASS SHA=$SHA HTTP_8085_HEALTH=$HEALTH HOME=$ROOT_CODE BACKUP=$BACKUP"
echo "Only Dashboard restarted; collector, T0, ledgers, R2, Native and Shadow untouched."
