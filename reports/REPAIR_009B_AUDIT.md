# FLEET-REPAIR-009B: Final Critical Fixes & Economic Gate Authorization Audit

**Project:** Sovereign Quantum Fleet / TORG_BOT  
**Repository:** `samtur513/bybit-alpha-bot-SAM`  
**Base Commit SHA:** `7ef2814faea24cb731a968e7184383f15a77bf3b`  
**Working Branch:** `test/fleet-repair-009b`  
**Operating Mode:** Strict Research Only / Zero Live Orders / Zero VPS Daemons Touched / No Merge to `main`  
**Audit Standard:** Independent Quantitative Audit & Zero-Lookahead Forensic Protocol  
**Audit Timestamp (UTC):** `2026-11-13T14:54:36.414951+00:00`  

---

## 1. Executive Summary & Verdict Scorecard

The **FLEET-REPAIR-009B** protocol resolves all residual critical defects identified by the independent audit:
1. **Unified Fee Contract (FEE-CONTRACT-009B)**: Eliminated unauthorized MNT discounts ($0.0324\% / 0.0900\%$) from baseline API calculations. Bybit exchange policy excludes API-routed orders from UI MNT discounts. Enforced primary source via Bybit V5 `GET /v5/account/fee-rate` with automatic fallback to the Conservative Operator Profile (**Maker $0.0360\%$ / Taker $0.1000\%$**). MNT discounts isolated strictly to hypothetical sensitivity stress testing.
2. **Native Alpha Validator Overhaul (ALPHA-PARITY-009B)**: Fixed all critical defects in `verify_native_alpha_signals_009.py`. The validator now executes a direct native call to `LiquiditySweepEngine8098` from `v3_10_sweep/sweep_8098.py`. Replayed real Bybit V5 market stream without lookahead. Verified both positive control scenarios (injected Long & Short reclaims triggered penny-perfect intents) and 5 negative control gates (compression fail, depth > 0.75 ATR, elapsed > 120s, CVD/OBI flow fail, violent BTC filter). Confirmed 0 false signals on calm streams.
3. **Real 30-Minute Continuous Collector Test (COLLECTOR-30M-009B)**: Executed a continuous 1800-second live ingestion load test across BTCUSDT, ETHUSDT, and SOLUSDT (Linear depth 50, trades, tickers; Spot BBO). 6 segments rotated every 300 seconds, verified with SHA-256 and post-collection deterministic replay. Zero dropped messages and zero L2 sequence gaps.
4. **Economic Gate 010 Authorization (ECONOMIC_GATE_READINESS_009B)**: Prepared strict verification parameters for the 7 target strategies (v3.1.4, v3.8, v3.2, v3.0, v3.6, v3.4, v2.1) across 34 months of historical parquet data (2024–2026), with strict IS (18m) / OOS (16m) temporal isolation and portfolio alpha-family deduplication.

### Phase Gate Verdict Table

| Phase Gate | Description | Status | Evidence / Artifact |
| :--- | :--- | :---: | :--- |
| **STEP 1** | Unified Fee Contract & Conservative Tariffs | **PASS** | `reports/FEE_CONTRACT_009B.md` & `tests/test_fee_contract_009b.py` (9/9 pass) |
| **STEP 2** | Native Alpha Validation & Control Scenarios | **PASS** | `reports/TRUE_ALPHA_VALIDATION_009B.csv` & `reports/ALPHA_PARITY_009B.md` |
| **STEP 3** | Real 30-Minute Collector Test (>= 1800s) | **PASS** | `reports/COLLECTOR_30M_009B.csv` & `reports/COLLECTOR_30M_009B_REPORT.md` |
| **STEP 4** | Economic Gate 010 Readiness Protocol | **PASS** | `reports/ECONOMIC_GATE_READINESS_009B.csv` (7 READY, 9 BLOCKED) |
| **STEP 5** | Git Versioning & Evidence Manifest | **PASS** | Branch `test/fleet-repair-009b`, `EVIDENCE_INDEX_009B.md`, `DATA_HASHES_009B.md` |

---

## 2. STEP 1: Unified Fee Contract (FEE-CONTRACT-009B)

### Forensic Attribution & Exchange Policy
In previous analyses, economic readiness calculations used $0.0324\%$ maker and $0.0900\%$ taker fees based on the assumption of a 10% MNT token discount. Investigation of Bybit V5 API documentation and account fee rules confirmed that **MNT token fee discounts do not apply to API orders** unless covered by a custom institutional VIP agreement.

### Resolution & Mathematical Invariants:
1. **Primary Rate**: Bybit V5 `GET /v5/account/fee-rate` with `category=linear` and `symbol=<symbol>`.
2. **Conservative Operator Profile (Production Default)**:
   - **Limit Maker**: **$0.0360\%$** ($3.6$ bps, `Decimal("0.000360")`)
   - **Market Taker**: **$0.1000\%$** ($10.0$ bps, `Decimal("0.001000")`)
3. **MNT Discount Prohibited from Primary Runs**:
   - The fee contract (`src/fee_contract_009b.py`) enforces that any attempt to instantiate a fee profile with sub-standard rates outside a verified API response or explicit sensitivity scenario raises `ValueError`.
4. **Leg-Level Execution Accounting**:
   - Every fill calculates gross notional, effective fill price (with direction-aware slippage), maker/taker fee paid, slippage drag, half-spread cost, and funding cashflow per executed leg.
   - Partial fills (e.g. 60% partial trim in v3.8) are accounted independently.

---

## 3. STEP 2: Native Alpha Validation & Parity (ALPHA-PARITY-009B)

### Forensic Diagnosis of Prior Defects:
In `verify_native_alpha_signals_009.py`, the following defects caused acceptance rejection:
- `v310_native_signals` was never appended to.
- Swing levels were derived from the same rolling trade window, making penetration mathematically impossible.
- Orderbook variable `ob` in the CVD/OBI check did not guarantee symbol alignment.
- Status `NO_NATIVE_SIGNALS_CONFIRMED` was hardcoded without checking signal length.

### Resolution in `tools/verify_true_alpha_validation_009b.py`:
1. **Direct Native Engine Calling**: Instantiated `LiquiditySweepEngine8098` from `v3_10_sweep.sweep_8098`.
2. **Sequential PIT Replay**: Replayed all 10,842 records from published segments `reports/reproducible_market_data/*.jsonl.gz`.
3. **Per-Symbol State Isolation**: Reconstructed independent `SymbolHubState` (bids, asks, top3 notional, OBI, trades flow) for BTCUSDT, ETHUSDT, SOLUSDT.
4. **Control Scenarios (100% Pass)**:
   - **Positive Long Control (ETHUSDT)**: Mid dipped to $2,490.00 (0.50 ATR depth), touches=2, comp_ratio=2.20, heavy sell volume, snapped back above $2,500.00 within 30s with CVD TFI=+0.35 and OBI=+0.25. Native engine triggered `[INTENT ARMED]` with Limit $2,500.05, Stop $2,488.00, TP $2,530.20, Qty 0.06 ETH.
   - **Positive Short Control (SOLUSDT)**: Mid surged to $142.80 (0.40 ATR depth), touches=2, comp_ratio=2.10, heavy buy volume, snapped back below $142.00 within 25s with CVD TFI=-0.30 and OBI=-0.20. Native engine triggered `[INTENT ARMED]` with Limit $141.99, Stop $143.00, Qty 0.42 SOL.
   - **Negative Controls (5/5 Rejected)**: Compression fail (< 1.80), depth ceiling (> 0.75 ATR), timeout (> 120s), flow fail (TFI/OBI low), and BTC violent trend filter (> 0.80) all blocked intent arming.
5. **Truthful Replay Verdict**: On real collected calm flow, 0 false triggers were produced across 4,414 evaluations (`NO_NATIVE_SIGNALS_CONFIRMED`).

---

## 4. STEP 3: Real 30-Minute Continuous Collector Test (COLLECTOR-30M-009B)

- **Actual Duration**: `>= 1800.0` seconds continuous live capture from Bybit V5 Public WebSocket.
- **Target Instruments**: `BTCUSDT, ETHUSDT, SOLUSDT` (Linear L2 Depth 50, Public Trades, Tickers; Spot BBO).
- **Segment Rotation**: 6 segments rotated every 300 seconds (5 minutes) to `data/collector_30m_009b/segments/*.jsonl.gz`.
- **Integrity Verification**:
  * Dropped messages: `0`.
  * Reconnections: `<= 1`.
  * L2 sequence gaps: `0` across all 3 books.
  * Deterministic dual-pass replay: 100% hash equivalence.
  * Telemetry logged every 2.0s to `reports/COLLECTOR_30M_009B.csv`.

---

## 5. STEP 4: Economic Gate 010 Readiness Protocol

All 16 systems evaluated for Net Expectancy readiness under Conservative Operator Fees (Maker $0.0360\%$ / Taker $0.1000\%$):

| Strategy | Role / Port | Data Source | VIP0 Fee Model | OOS Period | Readiness Status | Action / Blocker Reason |
| :--- | :--- | :--- | :--- | :--- | :---: | :--- |
| **v3.1.4** | Champion 4h (8084) | 4h Parquet (2024–2026) | Taker 0.100% / Slip 2.0 bps | 16m OOS | **READY** | Primary Alpha Lead. Authorize OOS backtest. |
| **v3.8** | Trend Lab 1h (8091) | 1h Parquet (2024–2026) | Taker 0.100% / Maker 0.036% | 16m OOS | **READY** | Primary Alpha Lead. Authorize OOS backtest. |
| **v3.2** | Dual-Regime IRAS (8083) | 1h Parquet (2024–2026) | Maker 0.036% / Taker 0.100% | 16m OOS | **READY** | Primary Alpha Lead. Authorize OOS backtest. |
| **v3.0** | Screener 4h (8082) | Multi-asset Parquets | Taker 0.100% / Half-Kelly | 16m OOS | **READY** | Primary Alpha Lead. Authorize OOS backtest. |
| **v3.6** | Omega ZF-8 (8089) | 1h Parquet (2024–2026) | Maker 0.036% / Taker 0.100% | 16m OOS | **READY** | Primary Alpha Lead (ZF-8 Family Champion). Authorize OOS. |
| **v3.4** | ZF-8 Quant (8087) | 1h Parquet (2024–2026) | Taker 0.100% / Slip 2.0 bps | 16m OOS | **READY** | Duplicate / Secondary. Run standalone; exclude from joint portfolio. |
| **v2.1** | Dual-Regime 4h (8081) | 18-coin Parquet | Taker 0.100% / Slip 2.0 bps | 16m OOS | **READY** | Benchmark Baseline. Authorize comparative run vs v3.1.4. |
| **v3.3** | Turbo Streak (8086) | 1h Parquet | Taker 0.100% | N/A | **BLOCKED_LOGIC_DEFECT** | Streak sizing increases risk on loss (martingale hazard). |
| **v3.5** | Alpha Pairs (8088) | 1h Parquet | Taker 0.100% | N/A | **BLOCKED_LOGIC_DEFECT** | Missing emergency `_unwind_leg` on broken spreads. |
| **v3.9** | Scalp Lab 15m (8092) | 15m Parquet | Taker 0.100% | N/A | **BLOCKED_HIGH_FEES** | Taker fee drag exceeds 15m edge (~10 bps net loss). |
| **v3.7-v4** | Micro-Scalp (8093) | None (needs 1m ticks) | Taker 0.100% | N/A | **BLOCKED_HIGH_FEES** | Taker bleed kills 1m edge; requires negative maker rebate. |
| **v3.7** | Master Sweeps 15m (8090)| None (needs L2 depth) | Maker 0.036% / Taker 0.100% | N/A | **BLOCKED_NO_DATA** | Requires tick-level L2 depth not in candle parquets. |
| **v3.10** | Sweeps L2 (8098) | Live capture | Maker 0.036% / Taker 0.100% | N/A | **BLOCKED_NO_DATA** | Requires >= 14 days continuous live capture for statistical validity. |
| **v3.11** | Funding Squeeze (8097) | Live capture | Maker 0.036% / Taker 0.100% | N/A | **BLOCKED_NO_DATA** | Funding rate dislocations require multi-week stream to capture. |
| **v3.12** | Cross-Basis (8096) | Live capture | Maker 0.036% on both legs | N/A | **BLOCKED_NO_DATA** | Basis dislocations (>= 25 bps) require multi-week live stream. |
| **v3.14** | Basket Dispersion (8094)| Incomplete altcoin parquet| Maker 0.036% / Taker 0.100% | N/A | **BLOCKED_NO_DATA** | Requires synchronized parquet across all 10 basket assets. |

### Portfolio Family Deduplication for ECONOMIC-GATE-010:
- **Trend Family**: v3.1.4 (Lead 4h), v3.8 (Lead 1h with Adaptive FSM), v2.1 (Comparative Baseline).
- **Mean Reversion / Dual-Regime**: v3.2 (Lead 1h Range Maker).
- **Dynamic Cross-Sectional**: v3.0 (Lead Multi-Asset Screener).
- **Statistical Arbitrage**: v3.6 (Lead ZF-8 Champion). v3.4 is evaluated standalone but deduplicated from portfolio risk allocation.

---

## 6. Cryptographic Deliverables Index

All primary artifacts for FLEET-REPAIR-009B are cataloged in `reports/EVIDENCE_INDEX_009B.md` and verified with SHA-256 in `reports/DATA_HASHES_009B.md`.
