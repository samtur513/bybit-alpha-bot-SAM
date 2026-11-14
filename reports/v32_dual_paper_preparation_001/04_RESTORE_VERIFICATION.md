# 04_RESTORE_VERIFICATION.md
## Independent Backup Integrity & Sandboxed Restoration Audit

- **Audit Date:** 2026-11-14 23:52 UTC
- **Working Branch:** `ops/v32-dual-paper-preparation-001`
- **Total Archives Packaged & Verified:** 19
- **Integrity Pass Rate:** **100.0% VERIFIED PASS**
- **Restoration Test Pass Rate:** **100.0% RESTORE_VERIFIED**

---

### 1. Restoration Verification Methodology

Each archive was subjected to automated verification in an isolated sandbox environment:
1. **Cryptographic Checksum:** SHA-256 calculated on the final immutable `.tar.gz` bundle.
2. **Decompression & Extraction:** Extracted into a temporary isolated filesystem (`tempfile.TemporaryDirectory`).
3. **Structure & Syntax Integrity:**
   - Checked that all Python files are syntactically valid.
   - Checked that all JSON state files (including `BOT_STATE_PERSISTENT_*.json` and live VPS snapshots) parse successfully without syntax corruption.
   - Verified that no process, thread, socket, or network order was spawned during extraction.
4. **Dual Storage Redundancy:** Every archive is stored in two independent locations:
   - Primary: `reports/v32_dual_paper_preparation_001/backups/`
   - Secondary: `.scratch/backups_pre_stop/`

---

### 2. Archive Verification Audit Table

| Target Port | System ID | Archive Name | Size (Bytes) | SHA-256 (First 16 chars) | Extracted Files | Restoration Status |
| :---: | :--- | :--- | :---: | :--- | :---: | :---: |
| **8080** | `v313_sector_arb` | `BACKUP_PORT_8080_V313_SECTOR_ARB.tar.gz` | 24481 | `632f3f829e984ae7...` | 7 | **`RESTORE_VERIFIED`** |
| **8081** | `v21_pure_quant` | `BACKUP_PORT_8081_V21_PURE_QUANT.tar.gz` | 180997 | `5e8d530ece9f9a3a...` | 71 | **`RESTORE_VERIFIED`** |
| **8082** | `v3_v4_exp` | `BACKUP_PORT_8082_V3_V4_EXP.tar.gz` | 25325 | `9595394e71fae2f9...` | 7 | **`RESTORE_VERIFIED`** |
| **8083** | `v32_dual_regime` | `V32_ORIGINAL_PRE_RESET_ARCHIVE.tar.gz` | 58122 | `b19024b291b3e68a...` | 16 | **`RESTORE_VERIFIED`** |
| **8084** | `v314_champion` | `BACKUP_PORT_8084_V314_CHAMPION.tar.gz` | 38427 | `5bc5f312498aa5ab...` | 7 | **`RESTORE_VERIFIED`** |
| **8085** | `meta_dashboard` | `BACKUP_PORT_8085_META_DASHBOARD.tar.gz` | 35476 | `ca541ae8b55e37b0...` | 5 | **`RESTORE_VERIFIED`** |
| **8086** | `v33_turbo_streak` | `BACKUP_PORT_8086_V33_TURBO_STREAK.tar.gz` | 30807 | `45ad147b0d160dfe...` | 13 | **`RESTORE_VERIFIED`** |
| **8087** | `v34_zf8_quant` | `BACKUP_PORT_8087_V34_ZF8_QUANT.tar.gz` | 33972 | `df439cbcd14fcf1b...` | 11 | **`RESTORE_VERIFIED`** |
| **8088** | `v35_alpha_pairs` | `BACKUP_PORT_8088_V35_ALPHA_PAIRS.tar.gz` | 30237 | `ed49eff97b9a2b11...` | 10 | **`RESTORE_VERIFIED`** |
| **8089** | `v36_omega_zf8` | `BACKUP_PORT_8089_V36_OMEGA_ZF8.tar.gz` | 28984 | `f6f5c6ce45cc0d44...` | 8 | **`RESTORE_VERIFIED`** |
| **8090** | `v37_master_sweeps` | `BACKUP_PORT_8090_V37_MASTER_SWEEPS.tar.gz` | 45114 | `acbf40ca8ad81ea4...` | 19 | **`RESTORE_VERIFIED`** |
| **8091** | `v38_newdragon_trend` | `BACKUP_PORT_8091_V38_NEWDRAGON_TREND.tar.gz` | 28688 | `87b578c93ce49d50...` | 10 | **`RESTORE_VERIFIED`** |
| **8092** | `v39_newdragon_scalp` | `BACKUP_PORT_8092_V39_NEWDRAGON_SCALP.tar.gz` | 24591 | `c2958279f8a0c6fc...` | 13 | **`RESTORE_VERIFIED`** |
| **8093** | `v37_v4_exp` | `BACKUP_PORT_8093_V37_V4_EXP.tar.gz` | 29790 | `6673c1dc752d87e1...` | 9 | **`RESTORE_VERIFIED`** |
| **8094** | `v314_basket_dispersion` | `BACKUP_PORT_8094_V314_BASKET_DISPERSION.tar.gz` | 23300 | `a1e1ba63f012e888...` | 8 | **`RESTORE_VERIFIED`** |
| **8095** | `data_tca_hub_8095` | `BACKUP_PORT_8095_DATA_TCA_HUB_8095.tar.gz` | 20846 | `e00d1a705119b15f...` | 9 | **`RESTORE_VERIFIED`** |
| **8096** | `v312_crossbasis` | `BACKUP_PORT_8096_V312_CROSSBASIS.tar.gz` | 14358 | `a2d919cf1f109197...` | 4 | **`RESTORE_VERIFIED`** |
| **8097** | `v311_funding` | `BACKUP_PORT_8097_V311_FUNDING.tar.gz` | 14418 | `6ee13a7f78ffc003...` | 4 | **`RESTORE_VERIFIED`** |
| **8098** | `sweep_8098` | `BACKUP_PORT_8098_SWEEP_8098.tar.gz` | 20047 | `b043bf85dc96f54f...` | 9 | **`RESTORE_VERIFIED`** |

---

### 3. Step-by-Step Restoration Commands

If any stopped system ever needs to be restored to active operation on the VPS, follow this exact procedure:

```bash
# Example: Restoring Port 8084 (v3.1.4 Champion 4h)
APP_DIR="/opt/bybit-bot"
cd "$APP_DIR"

# 1. Extract the verified archive
tar -xzf "reports/v32_dual_paper_preparation_001/backups/BACKUP_PORT_8084_V314_CHAMPION.tar.gz" -C "$APP_DIR"

# 2. Verify state file integrity
python3 -m json.tool "$APP_DIR/v3_1_4/reports/BOT_STATE_PERSISTENT_V314.json" > /dev/null

# 3. Re-enable and start systemd service
sudo systemctl enable bybit-v314.service
sudo systemctl start bybit-v314.service

# 4. Verify live HTTP telemetry
curl -s http://127.0.0.1:8084/api/status | jq .
```

All 18 systems have identical deterministic restoration procedures.
