# EVIDENCE_INDEX_VERIFY_003.md
## Master Evidence & Artifact Manifest: V32-LEGACY-VERIFY-003

- **Audit Target:** Original Bybit Multi-Alpha v3.2 Dual-Regime (Port 8083)
- **Repository:** `samtur513/bybit-alpha-bot-SAM`
- **Working Branch:** `audit/v32-legacy-verify-003`
- **Audit Standard:** Strict Forensic Research Only / Zero Live Orders / Zero VPS Restarts
- **Audit Timestamp:** 2026-11-14 23:15 UTC

---

### 1. Verification Deliverables (7 Core Artifacts)

| # | Relative Path | SHA-256 Hash | Size (Bytes) | Lines | Purpose & Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 01 | `reports/v32_legacy_verify_003/01_PRICE_EVIDENCE_CORRECTION.csv` | `ec72afb13dfdb58359de85c1bd1074c67142ea0fde01128c9cb2f4bf862378f7` | 7843 | 29 | Strict Zero-Tolerance Price Bounds Audit for all 28 trades (uncovers 2 slippage overshoots) |
| 02 | `reports/v32_legacy_verify_003/02_CASCADE_TP_EVENT_LEDGER.csv` | `7f015354117a229ff165df1856270183897aea15fde41658d38687c37887a501` | 4575 | 16 | Bybit 1-minute kline chronological sequence verification for all 15 partial exit trades |
| 03 | `reports/v32_legacy_verify_003/03_CASCADE_EXECUTION_PROOF.md` | `0fce4b1896048d0bb3ba634bfbf753757c104a2bef2e14227b731b8559e1c86b` | 3312 | 58 | Detailed technical proof & mathematical analysis of Cascade TP live tick execution |
| 04 | `reports/v32_legacy_verify_003/04_ECONOMIC_RECALCULATION.csv` | `230049ad017fd6ad394a7927d16c5f32ec112e3b5546a8e472021d9267b5b88a` | 1230 | 9 | Multi-scenario economic matrix across VIP0 fees (0.10%), variable slippage, and outlier exclusions |
| 05 | `reports/v32_legacy_verify_003/05_PIT_LIFECYCLE_AUDIT.csv` | `15f50f01ec0c7f4845c7ca8d54e5974f2d631ddc8d4b6bdb665d7b18aeaf5045` | 7698 | 29 | Point-in-Time (PIT) lifecycle breakdown, 1h candle boundary alignment, and `shift(1)` verification |
| 06 | `reports/v32_legacy_verify_003/06_FINAL_TRADE_VERDICT.csv` | `4d6f535b156390e288bd67dd4b0c3b23cadf2680a7a80a7183291a15d94dd10f` | 4478 | 29 | Comprehensive verdict and tier classification for each of the 28 trades |
| 07 | `reports/v32_legacy_verify_003/07_FINAL_ECONOMIC_VERDICT.md` | `58fa8be5221adb894cf980e81fb4ada09f6e3e02c90e54a4ea8d2941f5772c22` | 5504 | 41 | Final independent economic verdict, PnL decomposition, and answers to the 5 mandatory audit questions |

---

### 2. Verification Test Suites & Audit Tools

| # | Relative Path | SHA-256 Hash | Size (Bytes) | Lines | Purpose & Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| T01 | `tests/test_v32_legacy_verify_003_cascade_and_pit.py` | `9c74affc67c5cb4c979566c396b9d16affc62d9e171b4bcb9e4766169753f48a` | 5445 | 128 | PyTest test suite verifying strict zero-tolerance bounds, 1m sequence audit, and PIT rules (100% PASS) |
| T02 | `tools/build_v32_legacy_verify_003_dossier.py` | `516f86b6b67cd19b32628655887c19af5db7137b3dda2693bf3c6c4fe2e4becb` | 27514 | 474 | Master generator script that constructs all deliverables using live Bybit market klines |

---

### 3. Summary of Verification Results

- **Total Closed Trades Audited:** 28 trades.
- **Strict Zero-Tolerance Price Bounds (P0):**
  - Strictly within Bybit 1h [Low, High] bar: **26 / 28 (92.9% STRICT_PASS)**
  - Synthetic Slippage Overshoot: **2 / 28 (7.1% SLIPPAGE_OVERSHOOT_REJECT)**
    - `V32_TR_06` (XRPUSDT): Entry at 1.206259 vs Bar Low 1.206400 (overshoot -$0.000141 due to 0.02% slip on 1.2065 open).
    - `V32_TR_03` (NEARUSDT): Exit at 3.887777 vs Bar High 3.887000 (overshoot +$0.000777 due to 0.02% slip on 3.8870 stop).
  - Out-of-Bounds Rejections: **0 / 28 (0.0% OUT_OF_BOUNDS_REJECT)**.
- **1-Minute Cascade Take Profit Sequence (P1):**
  - Confirmed via 1-minute historical Bybit klines: **8 / 15 (53.3% MARKET_SEQUENCE_VERIFIED)**
  - Unresolved sequence (ratcheted stop crossed earlier in trade history): **7 / 15 (46.7% UNRESOLVED_SEQUENCE)**
- **Point-in-Time (PIT) & Lookahead Invariants (P3):**
  - 1H Timeframe and 120-bar lookback confirmed: **28 / 28 (100% PASS)**
  - Zero-Lookahead (`shift(1)` on closed bars) confirmed: **28 / 28 (100% PASS)**
- **Economic Viability Matrix (P2):**
  - Baseline Reported (0.09% fee, 0.02% slip): **+$17.99 USDT** (PF 2.47)
  - Realistic VIP0 (0.10% fee, 0.05% slip): **+$16.46 USDT** (PF 2.26)
  - Severe Stress without Outlier AKE (0.10% fee, 0.10% slip): **+$7.82 USDT** (PF 1.51)
  - Strictly Independently Verified Subset (13 trades): **-$9.67 USDT** (PF 0.21)
  - Market Sequence Verified Subset (21 trades): **+$7.42 USDT** (PF 1.88).

---

### 4. Integrity & Reproducibility Statement

All artifacts in this manifest are strictly immutable and cryptographically verified. Any modification to source data, trade records, or test logic will alter the SHA-256 checksums recorded above.
