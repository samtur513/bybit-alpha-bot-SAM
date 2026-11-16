# R2-CPU-RECOVERY-002 — Explicitly approved CPU-load gate removal

## User decision and evidence (2026-11-16)

The user runs parallel research tasks on the same VPS and explicitly requests
removal of the CryptoLab R2 readiness fatal threshold at
`load_per_cpu > 0.8`. Evidence captured by the independent monitor:

- 2026-11-16T15:50:38.863674Z: `load_per_cpu = 0.891113`,
  `LOAD_PER_CPU_GT_0_8` only.
- 2026-11-16T15:50:44Z: R2 process exits with
  `READINESS_RESOURCE_LIMITS`.
- 2026-11-16T15:50:59Z: R2 systemd restarts.
- Coincident measurement makes the CPU-load gate the highly supported cause,
  without proving which competing process originated that host load.

## Exactly what changes

`vendor/cryptolab_r2/research/hunter001_dg001/r2_shadow/readiness.py`:
delete only `or sample["load_per_cpu"]>0.8` from the fatal expression.
Keep load sampled and validated (finite, nonnegative) for diagnostics.

Preserved fatal gates:
1. Available RAM >=512 MiB
2. Free disk reserve >=max(0,10GiB-runtime_bytes)+512MiB
3. Free inodes >=4096
4. File-descriptor soft limit >=1024
5. Runtime file count <=3000
6. Runtime bytes <=10 GiB

Protected frozen R2 strategy, FROZEN_MANIFEST, cohort/T0, 24 accounts,
readiness evidence, public data provenance, observation schedule and
read-only/paper-trading permissions are unchanged.

`RECOVERY_PATCH.json` keeps `R2-RECOVERY-001` as required by the existing
runtime validator, but updates only the approved `readiness.py` hash and
records the previous approved hash for traceability.

The external monitor `tools/r2_resource_observer.py` reports high CPU
load as **advisory**, not `would_fail_readiness=true`. The live monitor
needs a separate file refresh/restart to reflect this after R2 deployment.

## Deployment

Do not manually hot-edit an active R2 process: R2 hashes all Python files
at startup, and hot editing would generate `SHADOW_CODE_CHANGED`.

Use `tools/deploy_r2_load_gate_recovery.sh` with a pinned full commit SHA.
It stages the two approved files via Git object, verifies current live
approval and exact candidate SHA256, reads existing COHORT/START_RECEIPT,
checks the 24-account SQLite ledger, validates all six gates with synthetic
samples, and supports:

- `--check`: no service stop, no source or DB changes.
- `--apply`: verified source/approval backups; stops only R2; copies only
  readiness and its RECOVERY_PATCH; starts only R2; verifies 34 seconds
  without automatic restarts. If deployment fails, restores original source
  and recovery patch and attempts to start the old service.

The installer refuses deployment from 23:45 to 00:15 UTC to protect the
daily R2 observation event. It never executes git checkout/pull/reset/clean
in the live worktree, touches trade data, restarts Native, Shadow,
original v3.2, Dashboard, VPN, or changes user authorization.

**After deployment:** new CPU spikes can slow R2 or cause missed
observation windows. No longer deliberately terminating the process does
not guarantee timely decisions, and the other six safety gates still apply.

## Verification

CI: `tests/test_r2_remove_cpu_gate.py` checks high loads do not cause fatal
readiness while each of six other thresholds still aborts; exact current
recovery SHA256 matches the patched source. Legacy resource observer tests
also updated for advisory CPU load.
