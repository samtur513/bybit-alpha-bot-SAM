#!/usr/bin/env bash
# Authorized recovery: remove ONLY the fatal R2 CPU load check.
# Usage: bash deploy_r2_load_gate_recovery.sh <40-char-commit-SHA> --check
#        bash deploy_r2_load_gate_recovery.sh <40-char-commit-SHA> --apply
set -Eeuo pipefail
REPO=/opt/bybit-shadow/bybit-alpha-bot-SAM
DIR=vendor/cryptolab_r2/research/hunter001_dg001/r2_shadow
DATA=/opt/bybit-shadow/data/cryptolab
SVC=bybit-shadow-r2.service
BRANCH=fix/r2-remove-cpu-load-kill-20261116
PY=/opt/bybit-bot/venv/bin/python
SHA="${1:-}"
MODE="${2:---check}"
STAGE=""
BACKUP=""
STOPPED=0
SUCCESS=0

exit_handler() {
  local code=$?
  trap - EXIT
  if [[ "$SUCCESS" != 1 && "$STOPPED" == 1 ]]; then
    echo "ROLLBACK: restoring prior R2 recovery code from $BACKUP" >&2
    # Never modify a running R2 Python file: it checks source hashes every tick.
    systemctl stop "$SVC" || true
    [[ -f "$BACKUP/readiness.py" ]] && cp -a "$BACKUP/readiness.py" "$REPO/$DIR/readiness.py"
    [[ -f "$BACKUP/RECOVERY_PATCH.json" ]] && cp -a "$BACKUP/RECOVERY_PATCH.json" "$REPO/$DIR/RECOVERY_PATCH.json"
    systemctl start "$SVC" || true
    echo "ROLLBACK attempted; check service state and logs. Backup=$BACKUP" >&2
  fi
  if [[ -n "$STAGE" && -d "$STAGE" ]]; then rm -rf "$STAGE"; fi
  exit "$code"
}
trap exit_handler EXIT
die() { echo "BLOCKED: $*" >&2; exit 1; }

[[ "$EUID" == 0 ]] || die "root required"
[[ "$SHA" =~ ^[0-9a-f]{40}$ ]] || die "exact 40-char SHA required"
[[ "$MODE" == --check || "$MODE" == --apply ]] || die "use --check or --apply"
[[ -x "$PY" && -d "$REPO/.git" && -f "$DATA/COHORT.json" ]] || die "unexpected host layout"
[[ -f "$DATA/START_RECEIPT.json" && -f "$DATA/observer.sqlite" ]] || die "R2 research history missing"
systemctl is-active --quiet "$SVC" || die "R2 is not currently active"
# On the live VPS systemd may launch R2 via a shell wrapper or a relative
# runtime.py path. ExecStart is not a reliable absolute-path identity check.
# We target only the exact systemd unit, require it running, and cryptographically
# validate its existing R2 source/approval plus the cohort/24-profile ledger
# before replacing *any* files.
[[ "$(systemctl show "$SVC" -p LoadState --value)" == loaded ]] ||
  die "R2 unit not loaded"
# A rapidly restarting R2 may briefly report MainPID=0 while systemd still
# considers the unit active. Source SHA+approved patch+cohort/ledger identity,
# not a transient PID snapshot, guard the actual installation target.
# Avoid daily signal boundary UTC 00:00.
utc_hhmm=$(date -u +%H:%M)
if [[ "$utc_hhmm" > "23:45" || "$utc_hhmm" < "00:15" ]]; then
  die "R2 daily observation window 23:45–00:15 UTC; do not restart"
fi

cd "$REPO"
git fetch origin "$BRANCH"
git cat-file -e "${SHA}^{commit}" || die "release commit not fetched"
git merge-base --is-ancestor "$SHA" "origin/$BRANCH" || die "SHA not in authorized branch"

STAGE=$(mktemp -d /opt/bybit-shadow/r2-cpu-stage.XXXXXXXX)
git show "${SHA}:$DIR/readiness.py" > "$STAGE/readiness.py"
git show "${SHA}:$DIR/RECOVERY_PATCH.json" > "$STAGE/RECOVERY_PATCH.json"
git show "${SHA}:tools/r2_resource_observer.py" > "$STAGE/r2_resource_observer.py"
"$PY" -m py_compile "$STAGE/readiness.py" "$STAGE/r2_resource_observer.py"

"$PY" - "$STAGE" "$REPO/$DIR" "$DATA" <<'PYCHECK'
import hashlib,importlib.util,json,pathlib,sqlite3,sys
stage,src,root=map(pathlib.Path,sys.argv[1:])
digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
old=json.loads((src/"RECOVERY_PATCH.json").read_text())
new=json.loads((stage/"RECOVERY_PATCH.json").read_text())
assert old["protocol"] == new["protocol"] == "R2-RECOVERY-001"
assert old["approved_patch"]["runtime.py"] == new["approved_patch"]["runtime.py"]
assert digest(src/"readiness.py")==old["approved_patch"]["readiness.py"],"unexpected current R2 patch"
assert new["previous_approved_readiness_sha256"]==digest(src/"readiness.py"),"unexpected old hash"
assert digest(stage/"readiness.py")==new["approved_patch"]["readiness.py"],"new patch hash mismatch"
cohort=json.loads((root/"COHORT.json").read_text())
receipt=json.loads((root/"START_RECEIPT.json").read_text())
assert receipt["cohort"]==cohort and receipt["status"] in ("ARMED","STARTED_UNVERIFIED","OBSERVING")
with sqlite3.connect((root/"observer.sqlite").as_uri()+"?mode=ro",uri=True,timeout=10) as db:
    assert db.execute("PRAGMA quick_check").fetchone()[0]=="ok"
    assert db.execute("SELECT count(*) FROM accounts").fetchone()[0]==24
spec=importlib.util.spec_from_file_location("readiness_candidate",stage/"readiness.py")
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
sample=dict(free_disk_bytes=20*1024**3,mem_available_bytes=1024**3,free_inodes=100000,
            fd_soft_limit=65535,load_per_cpu=10.0,runtime_files=20,runtime_bytes=1024**2)
assert mod.check_resources(sample)==sample,"high CPU unexpectedly fatal"
for field,value in [("free_disk_bytes",0),("mem_available_bytes",0),("free_inodes",0),
                    ("fd_soft_limit",0),("runtime_files",3001),("runtime_bytes",11*1024**3)]:
    bad=dict(sample);bad[field]=value
    try:mod.check_resources(bad)
    except RuntimeError as exc:
        assert str(exc)=="READINESS_RESOURCE_LIMITS"
    else:raise AssertionError("resource protection removed: "+field)
print("R2_RECOVERY_PREFLIGHT_PASS: CPU load advisory, all six resource gates intact, 24 accounts")
PYCHECK

if [[ "$MODE" == --check ]]; then
  SUCCESS=1
  echo "R2_CPU_CHECK_PASS: no R2 restart or file modifications"
  exit 0
fi

BACKUP=$(mktemp -d /opt/bybit-shadow/r2-cpu-backup.XXXXXXXX)
cp -a "$REPO/$DIR/readiness.py" "$BACKUP/readiness.py"
cp -a "$REPO/$DIR/RECOVERY_PATCH.json" "$BACKUP/RECOVERY_PATCH.json"
cp -a "$DATA/COHORT.json" "$BACKUP/cohort.reference"
cp -a "$DATA/START_RECEIPT.json" "$BACKUP/receipt.reference"
cmp -s "$BACKUP/readiness.py" "$REPO/$DIR/readiness.py" || die "backup verification failed"
cmp -s "$BACKUP/RECOVERY_PATCH.json" "$REPO/$DIR/RECOVERY_PATCH.json" || die "patch backup verification failed"
printf '%s\n' "$SHA" > "$BACKUP/release_sha.txt"
echo "VERIFIED_BACKUP: $BACKUP"

# IMPORTANT: frozen runtime rechecks all Python files every tick.
# Stop R2 before replacing readiness source and approval.
STOPPED=1
systemctl stop "$SVC"
! systemctl is-active --quiet "$SVC" || die "R2 failed to stop"
install -m 0644 "$STAGE/readiness.py" "$REPO/$DIR/readiness.py"
install -m 0644 "$STAGE/RECOVERY_PATCH.json" "$REPO/$DIR/RECOVERY_PATCH.json"
cmp -s "$STAGE/readiness.py" "$REPO/$DIR/readiness.py" || die "readiness copy error"
cmp -s "$STAGE/RECOVERY_PATCH.json" "$REPO/$DIR/RECOVERY_PATCH.json" || die "approval copy error"
cmp -s "$DATA/COHORT.json" "$BACKUP/cohort.reference" || die "cohort identity changed"
cmp -s "$DATA/START_RECEIPT.json" "$BACKUP/receipt.reference" || die "start receipt identity changed"

systemctl start "$SVC"
systemctl is-active --quiet "$SVC" || die "R2 not active after start"
before=$(systemctl show "$SVC" -p NRestarts --value)
sleep 34
systemctl is-active --quiet "$SVC" || die "R2 not active after two ticks"
after=$(systemctl show "$SVC" -p NRestarts --value)
[[ "$before" == "$after" ]] || die "R2 still auto-restarted"
SUCCESS=1
echo "R2_CPU_GATE_REMOVAL_PASS SHA=$SHA R2_ACTIVE=yes NRestarts=$after BACKUP=$BACKUP"
echo "Frozen strategies, cohort/T0, 24 accounts and ledger were not modified by installer."

# Repair the independent monitor too: an earlier terminal command ran outside the
# git repository and could have copied an empty file to this path.
# Monitor failure must NOT undo a successful protected R2 recovery.
MONITOR=/opt/bybit-shadow/diagnostics/r2_resource_observer.py
if [[ -d /opt/bybit-shadow/diagnostics ]]; then
  if [[ -e "$MONITOR" ]]; then
    cp -a "$MONITOR" "$BACKUP/r2_resource_observer.py.previous" ||
      echo "WARN: old monitor backup failed" >&2
  fi
  if install -m 0644 "$STAGE/r2_resource_observer.py" "$MONITOR" &&
     cmp -s "$STAGE/r2_resource_observer.py" "$MONITOR"; then
    echo "R2_MONITOR_FILE_RESTORED: CPU above 0.8 is advisory"
    if [[ "$(systemctl show r2-resource-watch.service -p LoadState --value 2>/dev/null)" == loaded ]]; then
      if systemctl restart r2-resource-watch.service &&
         systemctl is-active --quiet r2-resource-watch.service; then
        echo "R2_MONITOR_ACTIVE_PASS"
      else
        echo "WARN: monitor restart failed; R2 recovery remains applied" >&2
      fi
    else
      echo "WARN: monitor unit not loaded; diagnostic file restored" >&2
    fi
  else
    echo "WARN: monitor file install failed; R2 recovery remains applied" >&2
  fi
else
  echo "WARN: independent monitor directory absent; R2 recovery remains applied" >&2
fi
