# EVIDENCE_INDEX_V32.md
## Master Evidence & Artifact Manifest: V32-LEGACY-FORENSIC-001

- **Audit Target:** Original Bybit Multi-Alpha v3.2 Dual-Regime (Port 8083)
- **Repository:** `samtur513/bybit-alpha-bot-SAM`
- **Working Branch:** `audit/v32-legacy-forensic-001`
- **Audit Mode:** Strict FORENSIC RESEARCH ONLY (Zero live orders, zero VPS modifications)
- **Snapshot Timestamp:** 2026-11-14 19:01:01 – 19:18:24 UTC

---

### 1. Forensic Audit Deliverables (15 Artifacts)

| # | Relative Path | SHA-256 Hash | Size (Bytes) | Lines | Purpose & Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 01 | `reports/v32_legacy_forensic_001/01_LEGACY_RUNTIME_IDENTITY.md` | `63c9d9f3a4304243c3353c5a62eed82818fa88f0e0b5dacf8b36b54a3499d7b5` | 4403 | 38 | Architecture & Topology Specification (Answers 12 identification questions) |
| 02 | `reports/v32_legacy_forensic_001/02_LEGACY_SOURCE_MANIFEST.csv` | `98f4c21e824a9f761c438e63fa9f89e33e263d80fb01381ccb16e37ef2d8d2d0` | 1230 | 11 | Codebase Source Manifest with SHA-256 for all 9 v3.2 runtime files |
| 03 | `reports/v32_legacy_forensic_001/03_LEGACY_SNAPSHOT_PROVENANCE.md` | `46ec369cb91c94bcd7b9381aaf3b80471422e2758ef7577094b50039eb155ac8` | 1971 | 28 | Audit snapshot provenance, endpoints, timestamps, and read-only protocol |
| 04 | `reports/v32_legacy_forensic_001/04_LEGACY_FULL_TRADE_LEDGER.csv` | `da4e2ddb26c10fbc70c67c72d2cba533a6394098e682e3655271b29919d0b5aa` | 7062 | 29 | Complete Trade Ledger of all 28 closed trades with execution fees & metrics |
| 05 | `reports/v32_legacy_forensic_001/05_LEGACY_EQUITY_RECONCILIATION.csv` | `6de89ad6d395f48ba35897deebbb5e4c547d3016eb33434acea6768b8c47f64d` | 1274 | 12 | Penny-perfect mathematical reconciliation between $300, $321.88, and $325.76 |
| 06 | `reports/v32_legacy_forensic_001/06_LEGACY_FINANCIAL_AUDIT.md` | `70c1ee125a8158a8f5daaf02cf6eac084d4a4daea95fe7d35c884d40874478f9` | 2686 | 51 | Detailed Financial Audit resolving the dual PnL display and trade statistics |
| 07 | `reports/v32_legacy_forensic_001/07_LEGACY_KNOWN_DEFECTS_AUDIT.md` | `ce9f0000ebe41cbd32de72913d8c1747ca67a0206128e7480749619a1c2dd0ab` | 2920 | 74 | Exhaustive audit of Defects 01 (candle bug), 02 (fallbacks), 03, and 04 |
| 08 | `reports/v32_legacy_forensic_001/08_LEGACY_DEFECT_IMPACT.csv` | `290dd892d1ce4eeb6c9a55864094c6f0c8a6293828b6ea371395958a54420284` | 2399 | 5 | Quantitative defect impact assessment matrix across all strategy components |
| 09 | `reports/v32_legacy_forensic_001/09_LEGACY_EXECUTION_REALISM.md` | `8669745962e01eeaaa41a842a3b1b994b9670e7870dfbaf761960da4e1a1010b` | 1251 | 21 | Maker vs Taker order execution realism audit and fee structure verification |
| 10 | `reports/v32_legacy_forensic_001/10_LEGACY_FILL_AUDIT.csv` | `ab5b77d50e6cf0ee927f56bccc70db5c22d80b42899021d796259faf9937106a` | 2829 | 29 | Per-trade execution fill audit (slippage drag, latency risk, taker fee verification) |
| 11 | `reports/v32_legacy_forensic_001/11_LEGACY_STRATEGY_ATTRIBUTION.csv` | `bf8e4814a4183df4ff1fdde9bd1b2899b0675b3069ea1e607f5da18790f2096f` | 580 | 4 | Strategy Component Attribution (Trend 1h Donchian vs Range Mean-Reversion) |
| 12 | `reports/v32_legacy_forensic_001/12_LEGACY_SYMBOL_DAILY_PNL.csv` | `6db3eb78a0b4b95f98496d42460a82f7ca9e6d3fb7fd3827d8726ec9713ced6c` | 1208 | 29 | Symbol-level and daily PnL breakdown across the 12-day audit window |
| 13 | `reports/v32_legacy_forensic_001/13_LEGACY_PROFITABILITY_SCENARIOS.csv` | `12645697893d3f2c156a8653f248279ac39b47cd113453e5881c5dc0f377c639` | 707 | 5 | Profitability Stress Scenarios A, B, C, D (realistic slippage & win exclusions) |
| 14 | `reports/v32_legacy_forensic_001/14_LEGACY_PROFITABILITY_VERDICT.md` | `8b509e392e686b46e50f97a58f447d6908d374066182990188cc7a4bac7ccd28` | 2916 | 40 | Comprehensive final audit verdict and definitive answers to 5 core questions |
| 15 | `reports/v32_legacy_forensic_001/15_LEGACY_VS_NEW_CODE_DIFF.md` | `6b7a0d5c06bc2bea2cc31b759f64f73597b1f426665efdccdbf72afc6fdb5278` | 1771 | 20 | Architectural comparison between Original v3.2 Legacy and subsequent test adapters |

---

### 2. Frozen Raw VPS Snapshot Artifacts

| # | Relative Path | SHA-256 Hash | Size (Bytes) | Lines | Origin Endpoint & Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| S01 | `reports/v32_legacy_forensic_001/snapshot/dashboard_index.html` | `46519e2733bf085f44e7e0dfae03d8fbaee257145e638b85a8d1f030e1fb9b73` | 55227 | 933 | Raw HTML payload fetched from http://150.241.98.70:8083/ (55,227 bytes) |
| S02 | `reports/v32_legacy_forensic_001/snapshot/api_status.json` | `1576ad439652ddcdf5d3426376c49fb76a07469887f666df77c81882f2a406ab` | 24026 | 1 | Live status JSON fetched from http://150.241.98.70:8083/api/status (24,026 bytes) |
| S03 | `reports/v32_legacy_forensic_001/snapshot/api_state.json` | `31c3afb8d8688f5ff197f714c7213d376b95552740c17b62a4e7070e8cfb1a07` | 20878 | 680 | Persistent state JSON fetched from http://150.241.98.70:8083/api/state (20,878 bytes) |
| S04 | `reports/v32_legacy_forensic_001/snapshot/api_logs.json` | `a419398d0f1518648e05d633f659c0764c31d4dc0b13116b8bd088948272439b` | 39809 | 1 | Active logs JSON fetched from http://150.241.98.70:8083/api/logs (39,809 bytes) |

---

### 3. Verification Test Suites

| # | Relative Path | SHA-256 Hash | Size (Bytes) | Lines | Test Scope & Result |
| :--- | :--- | :--- | :--- | :--- | :--- |
| T01 | `tests/test_v32_legacy_financial_accounting.py` | `857f1b1e05ce6b98fbb0942e3174c9e8bbe24bf2f449a9e2c02347abf4c07d4e` | 4679 | 113 | PyTest suite verifying penny-perfect reconciliation of capital, fees, and PnL (Status: 100% PASS) |
| T02 | `tests/test_v32_legacy_synthetic_candle_defect.py` | `317df05239d71448c044e5d026b5689923008334f6ee3765f7b70a55baa44bc6` | 5328 | 165 | PyTest suite proving mathematical impossibility of limit fills under Defect 01 (Status: 100% PASS) |

---

### 4. Integrity & Reproducibility Statement

All artifacts in this manifest are strictly immutable and cryptographically verified. Any modification to source data, trade records, or test logic will alter the SHA-256 checksums recorded above.
