# EVIDENCE_INDEX_OPS_001.md
## Master Evidence & Artifact Manifest: TORG-FLEET-SAFE-STOP-001 + V32-DUAL-PAPER-START-001

- **Project:** TORG_BOT / Bybit Multi-Alpha Fleet
- **Working Branch:** `ops/v32-dual-paper-preparation-001`
- **Host Target:** VPS `150.241.98.70`
- **Audit Timestamp:** 2026-11-14 23:55 UTC
- **Evaluation Mode:** Safe Consolidation & Preparation (Zero live orders, zero unapproved service disruption)

---

### 1. Operational & Verification Deliverables (9 Core Artifacts)

| # | Artifact Path | SHA-256 Checksum | Size (Bytes) | Lines | Purpose & Description |
| :---: | :--- | :--- | :---: | :---: | :--- |
| 01 | `reports/v32_dual_paper_preparation_001/01_FLEET_INVENTORY.csv` | `69b793c037bef60ff8caacc7aee4aa037e83c6f6bee113fced6738e4e19607ff` | 6680 | 21 | Deliverable 01 |
| 02 | `reports/v32_dual_paper_preparation_001/02_DEPENDENCY_MAP.md` | `657f70dc11137252d6bc79c9f29b1e3a3ec242cdc6756027c8b528eaa3b69b75` | 4623 | 98 | Deliverable 02 |
| 03 | `reports/v32_dual_paper_preparation_001/03_BACKUP_MANIFEST.csv` | `00e6f328f649ede76cb7a28a45cdb4b6a9ebbade07d7b9b732d90ce9b16b9aa5` | 6740 | 20 | Deliverable 03 |
| 04 | `reports/v32_dual_paper_preparation_001/04_RESTORE_VERIFICATION.md` | `b94cac09d553d3689299ff831104aaafee0027513eaad70aec0e70738504eade` | 4952 | 76 | Deliverable 04 |
| 05 | `reports/v32_dual_paper_preparation_001/05_SAFE_STOP_REPORT.csv` | `83d225207d7ae9dbac2627f63ae4e98fd245661efc55e769a637237529887be0` | 7596 | 20 | Deliverable 05 |
| 06 | `reports/v32_dual_paper_preparation_001/06_FLEET_FINAL_STATUS.md` | `30d60b35a81e2d99cd24631c1f3e1636d908e73829b52bdfcee655fd04f25460` | 4720 | 62 | Deliverable 06 |
| 07 | `reports/v32_dual_paper_preparation_001/07_DUAL_START_CAPITAL_CONTRACT.md` | `853a5c4974f5c09620bfcf2ee90ca96b2009da457e04980cca3acaafaeaa9d62` | 3989 | 60 | Deliverable 07 |
| 08 | `reports/v32_dual_paper_preparation_001/08_T0_READINESS_CHECKLIST.md` | `c2a4ad31250f785ad078ab11a22ea12b0bf776cf5af8a7c559187e1b827a0106` | 2392 | 30 | Deliverable 08 |
| 09 | `reports/v32_dual_paper_preparation_001/09_DASHBOARD_SPECIFICATION.md` | `fab417be7a4eed82802a1ddce301862056a8f16306ddd98773cc018b864be849` | 3863 | 71 | Deliverable 09 |

---

### 2. Recoverable Backup Archives (19 Packages)

All archives are verified intact and tested in sandboxed restoration fixtures.
Primary location: `reports/v32_dual_paper_preparation_001/backups/`
Secondary location: `.scratch/backups_pre_stop/`

| Target Port | System ID | Archive Name | Size (Bytes) | SHA-256 Checksum |
| :---: | :--- | :--- | :---: | :--- |
| **8080** | `v313_sector_arb` | `BACKUP_PORT_8080_V313_SECTOR_ARB.tar.gz` | 24481 | `632f3f829e984ae717c1fabfd7fc7db362091d6459b97ec0cde165896a61da18` |
| **8081** | `v21_pure_quant` | `BACKUP_PORT_8081_V21_PURE_QUANT.tar.gz` | 180997 | `5e8d530ece9f9a3adad8d8d3f711d39a71704da9ef91b33982661ac4ca60e7f2` |
| **8082** | `v3_v4_exp` | `BACKUP_PORT_8082_V3_V4_EXP.tar.gz` | 25325 | `9595394e71fae2f9d9cffd96051f18203075ae1ef3c9bc851b27055b385b6b11` |
| **8083** | `v32_dual_regime` | `V32_ORIGINAL_PRE_RESET_ARCHIVE.tar.gz` | 58122 | `b19024b291b3e68ae54e81d2a5949ee6b4a902407f23521dc5e308a4ecb6c919` |
| **8084** | `v314_champion` | `BACKUP_PORT_8084_V314_CHAMPION.tar.gz` | 38427 | `5bc5f312498aa5abbb8ffe7abb9c3992235bf8ef7b95b4184e6f9baeac29dcc2` |
| **8085** | `meta_dashboard` | `BACKUP_PORT_8085_META_DASHBOARD.tar.gz` | 35476 | `ca541ae8b55e37b012a33ebeb73264448eebdee983f4246719b2a2e6c2259a59` |
| **8086** | `v33_turbo_streak` | `BACKUP_PORT_8086_V33_TURBO_STREAK.tar.gz` | 30807 | `45ad147b0d160dfed73ffa59155162d1e5de242f4bbf1f1665224f4e222baef3` |
| **8087** | `v34_zf8_quant` | `BACKUP_PORT_8087_V34_ZF8_QUANT.tar.gz` | 33972 | `df439cbcd14fcf1b6bca06e4207c8591a825baa7cf8e54674d3cf90c192160fe` |
| **8088** | `v35_alpha_pairs` | `BACKUP_PORT_8088_V35_ALPHA_PAIRS.tar.gz` | 30237 | `ed49eff97b9a2b11c8c487a8bfe6473eed54b427f2570042ae11aeeaad7fcb06` |
| **8089** | `v36_omega_zf8` | `BACKUP_PORT_8089_V36_OMEGA_ZF8.tar.gz` | 28984 | `f6f5c6ce45cc0d445b0710aecf8de98385f6b1a05363ee0190eb8081b76a1a9f` |
| **8090** | `v37_master_sweeps` | `BACKUP_PORT_8090_V37_MASTER_SWEEPS.tar.gz` | 45114 | `acbf40ca8ad81ea42c6d44b8390d5908f707ac41f45235c60305a77e9f90f286` |
| **8091** | `v38_newdragon_trend` | `BACKUP_PORT_8091_V38_NEWDRAGON_TREND.tar.gz` | 28688 | `87b578c93ce49d507e946f984c667e89c01d0d05cba19481049d8f3697a67650` |
| **8092** | `v39_newdragon_scalp` | `BACKUP_PORT_8092_V39_NEWDRAGON_SCALP.tar.gz` | 24591 | `c2958279f8a0c6fc789a213beef7135c9b32db9f0f7044b9ec53c6107d45d8b1` |
| **8093** | `v37_v4_exp` | `BACKUP_PORT_8093_V37_V4_EXP.tar.gz` | 29790 | `6673c1dc752d87e1137dc2293b018d5537efb163de8121f969aa2ae05371e0ae` |
| **8094** | `v314_basket_dispersion` | `BACKUP_PORT_8094_V314_BASKET_DISPERSION.tar.gz` | 23300 | `a1e1ba63f012e888f7748e56aef9de8a3d353aac0d8c918cecdf8c308436ef10` |
| **8095** | `data_tca_hub_8095` | `BACKUP_PORT_8095_DATA_TCA_HUB_8095.tar.gz` | 20846 | `e00d1a705119b15f740e48563de3849ebd6482143dd29d721f031d5174cf7bfe` |
| **8096** | `v312_crossbasis` | `BACKUP_PORT_8096_V312_CROSSBASIS.tar.gz` | 14358 | `a2d919cf1f1091977230707bc338465844c4af21f426543bf1cfdbe5e1d73acc` |
| **8097** | `v311_funding` | `BACKUP_PORT_8097_V311_FUNDING.tar.gz` | 14418 | `6ee13a7f78ffc0036eef1644a7b30f99c2c351de04a14c0e7100c4afb8ae0c1e` |
| **8098** | `sweep_8098` | `BACKUP_PORT_8098_SWEEP_8098.tar.gz` | 20047 | `b043bf85dc96f54f71d098f296e4e7c7ae629074957067b20bb9c13ad2beb507` |

---

### 3. Verification Test Suite

- **Test File:** `tests/test_v32_dual_paper_preparation.py`
- **Coverage:** Verifies fleet inventory completeness, dependency isolation, backup archive integrity, and starting capital contract invariants.
- **Pass Rate:** **100% PASS**.
