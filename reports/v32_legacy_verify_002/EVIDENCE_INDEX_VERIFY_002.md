# EVIDENCE_INDEX_VERIFY_002.md
## Master Evidence & Artifact Manifest: V32-LEGACY-VERIFY-002

- **Audit Target:** Original Bybit Multi-Alpha v3.2 Dual-Regime (Port 8083)
- **Repository:** `samtur513/bybit-alpha-bot-SAM`
- **Working Branch:** `audit/v32-legacy-verify-002`
- **Audit Standard:** Strict Forensic Research Only / Zero Live Orders / Zero VPS Restarts
- **Audit Timestamp:** 2026-11-14 22:55 UTC

---

### 1. Verification Deliverables (8 Artifacts)

| # | Relative Path | SHA-256 Hash | Size (Bytes) | Lines | Purpose & Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| 01 | `reports/v32_legacy_verify_002/01_AUDIT_CONTRADICTIONS.md` | `0ed6d770233ffcc39735031cf4548fb2b0d964b426e068176f4602fa6665aa1b` | 11100 | 162 | Audit Contradictions & Methodological Reconciliation (6 P0 items) |
| 02 | `reports/v32_legacy_verify_002/02_INDEPENDENT_EXECUTION_LEDGER.csv` | `21c8748b1b2426a4a8955864c5be322b15ec8c09041310f42cf7e63dce513fb0` | 8010 | 29 | Independent Trade-by-Trade Execution Ledger with fee & partial breakdown |
| 03 | `reports/v32_legacy_verify_002/03_ACCOUNTING_PROOF.md` | `5fe19dc5aa14bf2e69cd1b40d3efa88d51b625ce5c1449ce63ea2ada34d3c3a9` | 4391 | 63 | Accounting Proof & First-Principles PnL Mathematical Invariants |
| 04 | `reports/v32_legacy_verify_002/04_EXECUTION_EVIDENCE.csv` | `2a3e39337d84e084abcff84d692663b889a837e3e39aeee3911a2deda5f36068` | 5807 | 29 | Live Bybit V5 Public Market Kline Evidence for all 28 trades (High/Low bounds) |
| 05 | `reports/v32_legacy_verify_002/05_EXECUTION_REALISM_VERDICT.md` | `0bb85361d64e8c6ab10e207010d4d51b1026aa071b4b995ef8a0f6085ae634e8` | 2635 | 43 | Execution Realism, Liquidity, Slippage & Minimum Notional Verdict |
| 06 | `reports/v32_legacy_verify_002/06_LOOKAHEAD_AND_TIMING_AUDIT.md` | `edb7cb01c2c1e7ba5e5b32fdd100d550cb7ec96a9be72bdf4ae539ea47456794` | 2240 | 46 | Zero-Lookahead (PIT), Timeframe (1H/120 bars), and Margin Concurrency Audit |
| 07 | `reports/v32_legacy_verify_002/07_FINAL_ECONOMIC_COMPARISON.csv` | `88793c1e241f014427436a09f7defe4a1434ac14f8db590f6b9a2ab1203ec02a` | 882 | 7 | Economic Comparison Matrix: Reported vs Verified vs Conservative Stress |
| 08 | `reports/v32_legacy_verify_002/08_FINAL_VERDICT.md` | `38d4987ec24ef7f2ef43e4e1e158a54becdd59fe34a72971ea82c71b5de382a1` | 5656 | 52 | Final Independent Forensic Verdict & Answers to 5 Mandatory Questions |

---

### 2. Verification Test Suites & Audit Tools

| # | Relative Path | SHA-256 Hash | Size (Bytes) | Lines | Purpose & Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| T01 | `tests/test_v32_legacy_verify_002_invariants.py` | `baba087d3139435be151e56b9c8d2411cc492545cf2062571e95f182a17a48fa` | 4954 | 113 | PyTest test suite verifying independent accounting, Bybit bounds, and zero-lookahead (100% PASS) |
| T02 | `tools/build_v32_legacy_verify_dossier.py` | `e44d05717bb364aa927e6b98130e35687b1b3b4f0e1e16571bfb1562d563f2c0` | 28439 | 525 | Master script generating all 8 audit deliverables |
| T03 | `tools/fetch_v32_bybit_market_data.py` | `b6b20d2ebf0bce4fffec416b50bc57028bcf0c99d931654205048ee0adf050da` | 5300 | 146 | Script querying Bybit V5 Public REST API for historical klines |

---

### 3. Summary of Market Data Verification Results

- **Total Closed Trades Audited:** 28
- **Trades with Entry Price within Bybit 1h [Low, High] Bar:** **28 / 28 (100.0% PASS)**
- **Trades with Exit Price within Bybit 1h [Low, High] Bar:** **28 / 28 (100.0% PASS)**
- **Trades Meeting Bybit Minimum Notional (>= $5 USDT):** **28 / 28 (100.0% PASS)**
- **Non-Partial Trades Verified to < $10^-12 USDT from Primary Data:** **13 / 13 (100.0% PASS)**
- **Partial Trades Verified with Model-Derived Realized Gain:** **15 / 15 (100.0% PASS)**

---

### 4. Integrity & Reproducibility Statement

All artifacts in this manifest are strictly immutable and cryptographically verified. Any modification to source data, trade records, or test logic will alter the SHA-256 checksums recorded above.
