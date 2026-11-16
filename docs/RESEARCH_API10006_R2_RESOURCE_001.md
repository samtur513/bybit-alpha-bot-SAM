# RESEARCH-INFRA-001 — Bybit 10006 and CryptoLab R2 RESOURCE_LIMITS

**Scope:** Research-only Native + Realistic Shadow data-transport pacing and
an external read-only R2 resource observer. No strategy logic, no funding model,
no portfolio parameters, no trading credentials, no T0/cohort changes,
no changes to frozen R2 source/readiness, no changes to original v3.2 (8083),
VPN or active SQLite schema.

## Evidence — 16 November 2026

1. Native and Realistic Shadow each processed 18 symbols × 13/14 distinct
   closed 1h bars as independently shown by their SQLite ledgers.
2. Shadow logged repeated Bybit JSON `retCode=10006` responses for
   `/v5/market/kline` (e.g. 12:00 and 12:15 UTC). Native and Shadow runners
   were each polling the same unchanged 1h kline 18 times in every 5-second
   live-quote cycle. The public API's precise effective limit can be shared
   with other processes/UIDs; this does not prove the two runners alone caused
   every Bybit rate-limit response.
3. R2 exited at 2026-11-16T10:47:35Z with
   `RuntimeError: READINESS_RESOURCE_LIMITS`; auto-restarted after ~16 s;
   `NRestarts` observed at 1799. **The exact violated threshold was NOT
   persisted and has NOT been established.**
4. The installed R2 code's `readiness.check_resources` fails if any of:
   available host memory <512 MiB, per-CPU load average >0.8,
   free inodes <4096, open-file soft limit <1024, runtime files >3000,
   runtime bytes >10 GiB, or free disk bytes below
   max(0,10GiB-runtime_bytes)+512MiB. Separate `runtime.resource_gate`
   also checks file counts/sizes/free disk; neither gate can be weakened
   without explicit authorization and recovery-manifest review.

## Code fix — public 1h klines

`v3_2/src/runners/hourly_kline_gate.py` determines which symbols have a
missing, **already closed** 1h bar using Bybit server milliseconds.
`run_native_paper.py` and `run_shadow_paper.py` now:

- Preserve live 5s market tickers/position marking/funding updates.
- Request 1h klines only for symbols with an unprocessed bar, not every 5s.
- Stagger fresh-bar attempts: Native at +10s, Shadow at +25s UTC.
- Pace requests by 180 ms between symbols and at initial warmup.
- Retry failed/empty requests no sooner than 60s later; missing closed bars
  remain pending instead of being silently acknowledged.
- Keep original 50-bar chronological recovery, T0, deduplication,
  freshness and L2-provenance safeguards **unchanged**.

At the level of planned calls, an uninterrupted 18-symbol two-runner hour
goes from repeated 18×2 polls each 5s (~25,920 attempted kline requests/hour
at idealized loop speed) to ~36 kline fetches/hour; **actual reduction depends
on prior network latency and retries**. No guarantee of zero 10006 errors
from the other VPS processes or Bybit-side limits.

No live trades or margin parameters are touched.

## R2 diagnostics — no frozen-code modification

Run only the independent observer:

```bash
/opt/bybit-bot/venv/bin/python tools/r2_resource_observer.py \
  --root /opt/bybit-shadow/data/cryptolab
```

It prints one JSON snapshot of the seven source predicates and specifically
lists each currently violated limit. `measurement_evidence.fd_limit_source`
shows whether `fd_soft_limit` was retrieved from the running R2 process or
is merely the observer's fallback. A snapshot when R2 is healthy does NOT
identify the earlier 10:47 failure.

For intermittent faults, optionally run the same script as a separate,
low-priority, read-only systemd service (logging to journal only) at 15s
intervals. This monitor is outside R2 and does not alter its frozen files or
protected readiness policy. A violation captured immediately around a crash
will help identify the actual threshold. Interpret a `would_fail_readiness`
sample as evidence for **that sample**, not automatic proof of historical
causality.

## Safe release procedure

The branch contains `tools/deploy_research_rate_gate.sh`, which accepts a
specific 40-character SHA and **explicit** mode:

```bash
bash tools/deploy_research_rate_gate.sh <SHA> --prepare
bash tools/deploy_research_rate_gate.sh <SHA> --apply
```

Both commands require the existing Native/Shadow runners to be actively
running. Before installation, the script checks:

- Source files still match the known original runner blobs;
- Correct 18/18 latest closed bars per runner from SQLite;
- No open paper positions in either subsystem;
- UTC minute between +3 and +55 (avoids warmup skipping a just-closed bar);
- Exact pinned commit, blob hashes, Python syntax, independent R2 snapshot;
- Verified pre-change file backups before any modifications.

**--prepare does not restart or modify either trading service.** Only
`--apply` may install selective runner files and restart the two paper
services. All original files are retained for rollback. It never performs
`git checkout/pull/reset/clean` and never restarts R2, Dashboard, v3.2 or VPN.

### After release — separate evidence gates

1. Verify 18/18 closed bars appear after the *next* 1h UTC boundary in
   both SQLite sources. No missing T0 data or dedup duplicates.
2. Compare Bybit 10006 counts over an actual observation window before/after
   (prefer comparable hourly bars and workloads; no claims based only on
   one short period).
3. Check all services and PID/restart counts. R2 unchanged and independent.
4. Observe R2 numeric resource snapshots before changing hosting or policy.
   If load-per-CPU >0.8 confirmed, consider reducing extraneous VPS load or
   adding vCPU capacity; if memory/disk/inodes/FD limit fails, remedy that
   specific resource. Never guess and simply disable readiness.

**State of evidence:** Code staged + CI verified; live VPS patch and its
operational effect have **not** been verified until deployment and observation.
R2 historical trigger remains `TRIGGER_NOT_IDENTIFIED`.
