#!/usr/bin/env bash
# PINNED, SELECTIVE deployment of only Native/Shadow public market-data polling.
# Usage: bash tools/deploy_research_rate_gate.sh <40-char-SHA> --prepare
#        bash tools/deploy_research_rate_gate.sh <40-char-SHA> --apply
# --prepare is read-only with respect to trading DB and running services.
# --apply restarts only the two paper runners AFTER verifying all 18 latest bars
# have already been processed and neither paper portfolio has open positions.
set -Eeuo pipefail

REPO=/opt/bybit-shadow/bybit-alpha-bot-SAM
PY=/opt/bybit-bot/venv/bin/python
BRANCH=fix/research-market-data-resource-20261116
NATIVE=bybit-shadow-native.service
SHADOW=bybit-shadow-realistic.service
SHA="${1:-}"
MODE="${2:---prepare}"
FILES=(
  v3_2/src/runners/run_native_paper.py
  v3_2/src/runners/run_shadow_paper.py
  v3_2/src/runners/hourly_kline_gate.py
  tools/r2_resource_observer.py
)
# Verified original code blobs from VPS HEAD ba025ff7 (same as d8496795).
ORIG_NATIVE=db0871a35d9f24c1de228fed7bc0a8eb6ce18ede
ORIG_SHADOW=82fbd43a87513c87b1d91fadcd04a1731d4fe70e
STAGE=""
BACKUP=""
CHANGED=0
TRIED_RESTART=0

die() { echo "BLOCKED: $*" >&2; exit 1; }
cleanup() { [[ -z "$STAGE" || ! -d "$STAGE" ]] || rm -rf -- "$STAGE"; }
restore() {
  local status=$?
  trap - ERR
  if [[ "$CHANGED" == 1 && -n "$BACKUP" ]]; then
    echo "ROLLBACK: restoring exact previous files from $BACKUP" >&2
    for rel in "${FILES[@]}"; do
      if [[ -f "$BACKUP/$rel" ]]; then
        install -m 0644 "$BACKUP/$rel" "$REPO/$rel" || true
      elif [[ -f "$BACKUP/$rel.absent" ]]; then
        rm -f "$REPO/$rel"
      fi
    done
    if [[ "$TRIED_RESTART" == 1 ]]; then
      systemctl restart "$NATIVE" || true
      systemctl restart "$SHADOW" || true
    fi
  fi
  echo "INSTALL_BLOCKED_OR_ROLLED_BACK. Backup=$BACKUP" >&2
  exit "$status"
}
trap cleanup EXIT
trap restore ERR

[[ "$EUID" == 0 ]] || die "root is required"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || die "exact SHA required"
[[ "$MODE" == --prepare || "$MODE" == --apply ]] || die "use --prepare or --apply"
[[ -x "$PY" && -d "$REPO/.git" ]] || die "expected Python and Git repository missing"
systemctl is-active --quiet "$NATIVE" || die "Native not running"
systemctl is-active --quiet "$SHADOW" || die "Shadow not running"

cd "$REPO"
git fetch origin "$BRANCH"
git cat-file -e "${SHA}^{commit}"
git merge-base --is-ancestor "$SHA" "origin/$BRANCH" ||
  die "release SHA not on the expected branch"
[[ "$(git hash-object "$REPO/v3_2/src/runners/run_native_paper.py")" == "$ORIG_NATIVE" ]] ||
  die "Native runner was already modified; refuse to overwrite an unknown revision"
[[ "$(git hash-object "$REPO/v3_2/src/runners/run_shadow_paper.py")" == "$ORIG_SHADOW" ]] ||
  die "Shadow runner was already modified; refuse to overwrite an unknown revision"

# Critical: current bar processing and portfolios must be completely settled.
# This also avoids the pre-existing warmup-on-restart skipping an unprocessed bar.
"$PY" - <<'PYPRE'
from pathlib import Path
import json, sqlite3, time
root=Path("/opt/bybit-shadow/data")
hour=3_600_000
now=int(time.time()*1000)
within=now%hour
if not 180_000 <= within <= hour-300_000:
    raise SystemExit("BLOCKED: deploy between UTC hour +3min and +55min only")
expected=(now//hour-1)*hour
def connect(path):
    return sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True,timeout=5)
with connect(root/"native_ledger.db") as db:
    row=db.execute("SELECT state_json FROM native_state_journal ORDER BY journal_id DESC LIMIT 1").fetchone()
    if not row: raise SystemExit("BLOCKED: missing Native state")
    state=json.loads(row[0])
    bars=state.get("last_processed_bar_ts",{})
    if len(bars)!=18 or any(int(x)<expected for x in bars.values()):
        raise SystemExit("BLOCKED: Native not caught up on all 18 closed bars")
    if state.get("engine",{}).get("open_positions") or state.get("range_engine",{}).get("open_positions"):
        raise SystemExit("BLOCKED: Native has open paper positions")
with connect(root/"shadow_ledger.db") as db:
    bar_rows=db.execute("SELECT symbol,MAX(exchange_ts) FROM shadow_dedup_events GROUP BY symbol").fetchall()
    if len(bar_rows)!=18 or any(int(ts)<expected for _,ts in bar_rows):
        raise SystemExit("BLOCKED: Shadow not caught up on all 18 closed bars")
    row=db.execute("SELECT state_json FROM shadow_state_journal ORDER BY journal_id DESC LIMIT 1").fetchone()
    if not row: raise SystemExit("BLOCKED: missing Shadow state")
    state=json.loads(row[0])
    if state.get("shadow_portfolio",{}).get("positions"):
        raise SystemExit("BLOCKED: Shadow has open paper positions")
print("SAFE_WINDOW_PASS: all 36 latest bars processed, both portfolios have no positions")
PYPRE

STAGE=$(mktemp -d /opt/bybit-shadow/research-stage.XXXXXXXX)
for rel in "${FILES[@]}"; do
  mkdir -p "$STAGE/$(dirname "$rel")"
  git show "${SHA}:$rel" > "$STAGE/$rel"
  [[ "$(git hash-object "$STAGE/$rel")" == "$(git rev-parse "${SHA}:$rel")" ]] ||
    die "file checksum mismatch: $rel"
done
"$PY" -m py_compile "${STAGE}/v3_2/src/runners/run_native_paper.py" \
  "${STAGE}/v3_2/src/runners/run_shadow_paper.py" \
  "${STAGE}/v3_2/src/runners/hourly_kline_gate.py" \
  "${STAGE}/tools/r2_resource_observer.py"
echo "STAGING_SYNTAX_PASS / SHA=$SHA"

# R2 diagnostics can be sampled without ever modifying R2 code or its data.
"$PY" "${STAGE}/tools/r2_resource_observer.py" \
  --root /opt/bybit-shadow/data/cryptolab

if [[ "$MODE" == --prepare ]]; then
  echo "PREPARE_PASS: no runner stopped or modified; use --apply only after approval"
  exit 0
fi

BACKUP=$(mktemp -d /opt/bybit-shadow/research-backup.XXXXXXXX)
for rel in "${FILES[@]}"; do
  mkdir -p "$BACKUP/$(dirname "$rel")"
  if [[ -f "$REPO/$rel" ]]; then
    cp -a "$REPO/$rel" "$BACKUP/$rel"
    cmp -s "$REPO/$rel" "$BACKUP/$rel" || die "backup failed: $rel"
  else
    : > "$BACKUP/$rel.absent"
  fi
done
printf '%s\n' "$SHA" > "$BACKUP/release_sha.txt"
echo "Pre-change backup verified: $BACKUP"
CHANGED=1
for rel in "${FILES[@]}"; do
  install -m 0644 "$STAGE/$rel" "$REPO/$rel"
  cmp -s "$STAGE/$rel" "$REPO/$rel"
done
# Import under the actual VPS environment before any process is restarted.
"$PY" - <<'PYIMPORT'
from v3_2.src.runners.hourly_kline_gate import HourlyKlineGate
from v3_2.src.runners.run_native_paper import NativePaperRunner
from v3_2.src.runners.run_shadow_paper import ShadowPaperRunner
assert HourlyKlineGate and NativePaperRunner and ShadowPaperRunner
print("VPS_RUNNER_IMPORT_PASS")
PYIMPORT

# Isolated restarts; R2, original v3.2, Dashboard and VPN stay untouched.
TRIED_RESTART=1
systemctl restart "$NATIVE"
sleep 10
systemctl is-active --quiet "$NATIVE"
systemctl restart "$SHADOW"
sleep 10
systemctl is-active --quiet "$SHADOW"

echo "PAPER_RATE_GATE_DEPLOY_PASS: $SHA"
echo "Only $NATIVE and $SHADOW were restarted."
echo "Protected: CryptoLab R2, original v3.2 port 8083, Dashboard 8085, Xray, T0, all DBs."
echo "Previous exact files retained at: $BACKUP"
