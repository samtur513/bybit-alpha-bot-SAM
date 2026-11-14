# BACKUP_GAP_ANALYSIS.md
## Forensic Audit of Backup Gaps & Differentiation: Local Source vs. Live VPS State

**Audit Standard:** V32-MASTER-001 Blockers Remediation  
**Host Target:** VPS `150.241.98.70` (Linux x86_64, `/opt/bybit-bot/`)  
**Status:** **`CRITICAL_GAPS_IDENTIFIED` / `BLOCKED_NO_VPS_ACCESS`**  
**Audit Timestamp:** 2026-11-15 00:10 UTC  

---

### 1. The Fundamental Distinction: Local Source vs. Live VPS State

In the preceding preparation deliverable (`03_BACKUP_MANIFEST.csv`), archives were constructed by compressing the local Git repository source code and embedding an HTTP `/api/status` JSON response obtained over the network. 

**This does NOT constitute a complete, consistent, or verifiable backup of the running VPS.**

A rigorous quantitative trading infrastructure requires clear epistemic separation between:

1. **`LOCAL_SOURCE_BACKUP`**:
   - Represents the tracked Git codebase and static templates stored in the developer workstation.
   - Captures code syntax, default configs, and unit tests.
   - **Does NOT capture runtime disk state, live process memory, volatile WAL buffers, active SQLite locks, environment secrets, or uncommitted server-side hotfixes.**
2. **`LIVE_VPS_STATE_BACKUP`**:
   - Represents a byte-for-byte, crash-consistent snapshot of `/opt/bybit-bot/` on VPS `150.241.98.70`.
   - Captures real disk persistence files, WAL buffers, actual `.env` configuration, daemon process logs, and systemd overrides.

---

### 2. Comprehensive Inventory of Unobtained VPS Disk Assets

The following real filesystem assets on VPS `150.241.98.70` were **NOT obtained** and remain absent from the local archives:

| # | Asset Category | VPS Absolute Path | Purpose & Description | Consequence of Absence |
| :---: | :--- | :--- | :--- | :--- |
| **01** | **Live Persistent State Files** | `/opt/bybit-bot/v3_2/reports/BOT_STATE_PERSISTENT_V32.json` | Exact JSON disk state holding sequence counters, cooldown dicts, internal timer ticks, and risk manager state. | Restoring from HTTP snapshot creates schema mismatch and loses sub-second cooldown timers. |
| **02** | **Fleet State Files (15 Bots)** | `/opt/bybit-bot/v3_*/reports/BOT_STATE_PERSISTENT_*.json` | Live persistent state for ports 8080–8098. | Loss of historical bar counters and sequence continuity. |
| **03** | **SQLite Databases** | `/opt/bybit-bot/v3_10_sweep/tca_attribution.db` | Relational SQLite database recording tick-level TCA attribution. | Complete loss of tick-level transaction cost analytics. |
| **04** | **SQLite WAL Buffers** | `/opt/bybit-bot/v3_10_sweep/tca_attribution.db-wal` | Uncheckpointed WAL transactions in shared memory. | Database corruption if main `.db` is copied without WAL. |
| **05** | **Environment Secrets** | `/opt/bybit-bot/.env`, `/opt/bybit-bot/v3_*/.env` | API keys, secret passphrases, Telegram bot tokens, Webhook endpoints. | Restored daemons fail on startup due to missing environment. |
| **06** | **Systemd Unit Overrides** | `/etc/systemd/system/bybit-*.service.d/override.conf` | Custom CPU/RAM slice quotas, nice values, environment overrides. | Unplanned resource allocation regressions upon restore. |
| **07** | **Process Execution Logs** | `/var/log/journal/`, systemd stderr logs | Diagnostic traces of runtime exceptions, WebSocket dropouts, rate-limit warnings. | Inability to perform root-cause analysis on anomalous trades. |
| **08** | **Uncommitted Server Edits** | `/opt/bybit-bot/` untracked modifications | Manual hotfixes, script modifications, or parameter tweaks performed on the VPS via `nano`/`vim`. | Silent regression of server-side fixes upon redeployment. |

---

### 3. Invalidation of "RESTORE_VERIFIED" Status

In the previous report, the status `RESTORE_VERIFIED` was erroneously assigned based solely on extracting a `.tar.gz` bundle into a local temporary folder (`tempfile.TemporaryDirectory`) on Windows and verifying JSON syntax.

**This test only verified archive packaging mechanics, not actual service restoration.**

- **Status Revocation:** All 19 archives previously labeled `RESTORE_VERIFIED` are hereby formally reclassified as:
  `LOCAL_ARCHIVE_VALIDATED / LIVE_VPS_STATE_NOT_CAPTURED`
  $$\mathbf{LOCAL\_ARCHIVE\_VALIDATED \quad / \quad LIVE\_VPS\_STATE\_NOT\_CAPTURED}$$
- **Integrity Statement:** Until a true server-side disk snapshot is captured, transferred, and validated against the running processes, **no claim of complete backup or verified restoration can be made**.

---

### 4. VPS Access Status: `BLOCKED_NO_VPS_ACCESS`

- **Access Probe:** Direct SSH connection probe (`ssh -o BatchMode=yes root@150.241.98.70`) returns:
  ```text
  root@150.241.98.70: Permission denied (publickey,password).
  ```
- **Finding:** No valid SSH private key or password credentials exist in the local workspace environment.
- **Epistemic Conclusion:** Because direct shell access to `/opt/bybit-bot/` is not established, the automated agent is physically unable to capture disk files, WAL buffers, or `.env` configurations from the VPS.
- **Formal Status:** **`BLOCKED_NO_VPS_ACCESS`**.
- **Action Constraint:** In accordance with prompt directives, **all service stopping is strictly blocked** until authorized server-side snapshots are acquired.
