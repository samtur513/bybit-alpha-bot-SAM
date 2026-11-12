# DATA_GATE_IS.md: In-Sample Historical Data Gate & Accounting Verification

**Document ID**: `DATA_GATE_IS`  
**Phase**: F2.1 Data Gate Patch & F2.2 In-Sample Alpha Economic Validation  
**Evaluation Range**: `2024-01-01 00:00:00 UTC` to `2025-06-30 23:00:00 UTC` (18 Months, 13,128 Hourly Bars)  
**Target Architecture**: Bybit Linear Perpetuals Alpha Pairs (`v3.5`)  
**Git Commit SHAs**:
- `V35-ORIGINAL`: `43e8546` (baseline in `v3_5/`, strictly untouched)
- `V35-REPAIRED`: `43497db199be63c63f6724296a49075a93d73596` (hardened production engine in `v3_5_repaired/`)
- Branch: `test/f1a-regression-suite`

---

## 1. Temporal Bounds & Continuity Invariants

| Attribute | Specification | Verification Result |
| :--- | :--- | :--- |
| **In-Sample Start Timestamp** | `1704067200000` (`2024-01-01 00:00:00 UTC`) | **PASS** — Exact match across all active instruments |
| **In-Sample End Timestamp** | `1751324400000` (`2025-06-30 23:00:00 UTC`) | **PASS** — Exact match across all active instruments |
| **Total In-Sample Bar Count** | 13,128 bars ($18 \text{ months} \times 24 \text{ bars/day} + \text{leap day}$) | **PASS** — Chronological timeline of 13,128 consecutive 1h intervals |
| **Sampling Interval** | 3,600,000 ms (1 hour) | **PASS** — Zero gaps, zero nulls, zero duplicate timestamps |
| **Warm-Up Period** | 75 bars (`1704067200000` to `1704337200000`) | **PASS** — Indicators require 72h momentum + 48h KER; trading begins at bar 75 |
| **Active Trading Bars** | 13,053 bars (Bar index 75 to 13,127) | **PASS** — Evaluated point-in-time sequentially |

---

## 2. Point-in-Time Integrity & Zero-Lookahead Proof

To ensure complete causality and eliminate lookahead bias:
1. **Closed Bar Invariant ($t \to t+1$)**:
   - Indicator metrics ($Ret_{72}$, $KER_{48}$, $ATR_{24}$, $RS$) are computed strictly on closed bars up to bar $t$.
   - Any trade execution triggered by signals generated at bar $t$ executes at the beginning of bar $t+1$ at the prevailing Orderbook L1 Ask/Bid.
   - Closed bar timestamp (`bar_ts` / `current_ts`) strictly represents the end of the completed interval.
2. **Unified Timeline Slicing**:
   - The test harness loads the entire unified timeline $T = [t_0, t_1, \dots, t_{13127}]$.
   - At iteration $k$, the data passed to the engine is strictly restricted to records where $\text{timestamp} \le T[k]$.
   - No future prices, highs, lows, or volumes are accessible to `process_bar()`.
3. **Event-Driven Signal Parity**:
   - Metric and ranking parity test (`test_class_5_final_signal_decision_diff`) passes 100 consecutive bars with **zero** `SIGNAL_CHANGED` events.
   - Any synthetic data fallback has been permanently removed from the test suite: if any parquet file is missing or incomplete, the test immediately fails with `DATA_GATE_BLOCKED`.

---

## 3. Chronological Listing Date Verification (RENDERUSDT)

A critical flaw in preliminary data ingestion was the independent loading of `.head(N)` slices, which inadvertently matched July 2024 candles of newly listed coins with January 2024 candles of the benchmark.

### 3.1. Listing Date Audit
- **Instrument**: `RENDERUSDT`
- **Exchange Listing Time**: `2024-07-26 08:00:00 UTC` (`timestamp = 1721980800000`).
- **Timeline Index**: Bar index **4,976** of the 13,128-bar timeline.
- **Pre-Listing Bar Count (Jan 1, 2024 to Jul 26, 2024 07:00 UTC)**: **EXACTLY 0 BARS**.

### 3.2. Causal Universe Handling
```python
# Causal timeline slicing implementation
for sym, recs in symbol_records.items():
    start_idx = start_indices[sym]
    if bar_idx >= start_idx:
        offset = bar_idx - start_idx
        avail = recs[: offset + 1]
        if len(avail) >= 1:
            sub_series[sym] = avail
            curr_prices[sym] = Decimal(str(avail[-1]["close"]))
```
- For $0 \le \text{bar\_idx} < 4976$, `RENDERUSDT` is completely absent from `sub_series` and `curr_prices`.
- Universe size is dynamically 17 coins from bar 0 to 4,975, and 18 coins from bar 4,976 onward.
- `RENDERUSDT` becomes eligible for ranking only after accumulating the minimum lookback bars ($\ge 50$ bars post-listing).
- **Result**: Zero data bleeding, zero premature listing bias.

---

## 4. Historical Dataset Cryptographic Checksums (SHA-256)

All 18 instruments were verified from authentic Bybit Linear Perpetuals 1-hour OHLCV parquet files in `data/parquet_2024_2026/`:

| # | Symbol | Record Count | File Size (Bytes) | SHA-256 Checksum |
| :-: | :--- | :-: | :-: | :--- |
| 1 | **BTCUSDT** (Benchmark) | 24,610 | 1,285,038 | `17b4a02dd78ccd76d14ef63caa491744547c15fcc8b141f4c72f41a2ea1df9d3` |
| 2 | **ETHUSDT** | 24,610 | 1,244,900 | `84fbb5eb85083b951bb6027875d49269929dff2ff41ef1652857de0ad24e9d85` |
| 3 | **SOLUSDT** | 24,610 | 1,029,120 | `719729fc67a28975a142daf161fee24e3043ae3b68462bc06326b33ed4427df8` |
| 4 | **SUIUSDT** | 24,610 | 1,113,347 | `1ca3a54d730e313b976cf58db9b0acce2e82c0af310df29d0d572f22be70e881` |
| 5 | **DOGEUSDT** | 24,610 | 1,222,684 | `1a7a5e58e46b986cc7bfdfeedbc08631d6a5e76a115e4be4683fd53fa14314ed` |
| 6 | **AVAXUSDT** | 24,610 | 1,040,024 | `02d65889dea4cd7dde4931d74481f6c27a45f6a1f441dcba88cd41a0d9018905` |
| 7 | **NEARUSDT** | 24,610 | 992,534 | `24b9dab65fcc00e9526c5af9aa2ff8191a6f12b8ed349afeaf8618a8c1746782` |
| 8 | **LINKUSDT** | 24,610 | 1,033,742 | `b627af3a07ffefdfeaf71067bb51806be7ceb34326e335ee2e64dff36a5f57d6` |
| 9 | **APTUSDT** | 24,610 | 1,085,222 | `3e619adf8f4640f2f5f91c73227acaeacfc7d38dccbc9965f1e82bdf115a6ca5` |
| 10 | **ADAUSDT** | 24,610 | 958,775 | `35451434a9beec98d0ea912a8c03f9aeef245cea748e15205a2894a30be33c84` |
| 11 | **XRPUSDT** | 24,610 | 1,081,372 | `2cc629aa27053a94dc686f1bc07a58ac0ee9d2deaf4d426e1ab8fd729b5f1fac` |
| 12 | **BNBUSDT** | 24,610 | 896,591 | `c232b4872e66af0159d3ddd1e0fff10ac3d2d859ca123a2a3a796e803b704ffa` |
| 13 | **TIAUSDT** | 24,610 | 1,023,559 | `f39893903394a08bb060e43f7a98f2fa72e6d2753eab9aad74e14e65e7fdd8b9` |
| 14 | **INJUSDT** | 24,610 | 1,094,178 | `075a38a00285fdcb3e16fd0905891593d5cc54afe8e8d97b9292122e597103c6` |
| 15 | **ARBUSDT** | 24,610 | 1,127,162 | `4c951f88227d3bd821157e50676034acf82d5e094df4fbfe25ade8b692250c00` |
| 16 | **OPUSDT** | 24,610 | 1,183,624 | `18a6b75817e48294b9c3d5aa482f065edaec51645c19c75a7a0b2195e0d8bd51` |
| 17 | **1000PEPEUSDT** | 24,610 | 1,311,166 | `e57f735b20139485ff9fca189b6e852d706853bf20c6c919478805a3c993d8f8` |
| 18 | **RENDERUSDT** | 19,634 | 823,633 | `1a4c2b17c8289b9c6ab6e9deca017cde9737a26850eaff5a1c295cdb86171ccf` |

---

## 5. Penny-Perfect Financial Conservation & Reconciliation

### 5.1. The Exact Conservation Identity

Under double-entry ledger accounting, `Realized Equity` is debited by the entry fee immediately upon position entry ($E_{fee} = Q \times P_{entry} \times \text{fee\_rate}$). When a position is closed, the remaining equity adjustment equals:
$$\Delta \text{Equity}_{exit} = \text{Gross PnL} - \text{Exit Fee} + \text{Funding Cashflows}$$

Consequently, at any point in time $t$:
$$\text{Ending Realized Equity} = \text{Initial Capital} + \sum_{i \in \text{Closed}} \text{Net PnL}_i - \sum_{j \in \text{Open}} \text{Entry Fee}_j + \sum_{k} \text{Funding Cashflow}_k$$

Where:
$$\text{Net PnL}_i = \text{Gross PnL}_i - \text{Entry Fee}_i - \text{Exit Fee}_i$$

When all positions are closed, $\sum_{j \in \text{Open}} \text{Entry Fee}_j \equiv 0$, yielding exact parity between net closed PnL and realized equity delta.

---

### 5.2. Mathematical Dissection of the Preliminary 425-Bar Test

In the preliminary 425-bar test run, the auditor raised questions regarding:
1. Why Net PF was reported as 0.88 while Gross PF was 0.95.
2. The exact penny-perfect reconciliation of initial capital \$300.00 to ending equity \$293.1312.

#### A. Gross vs Net Profit Factor Breakdown
- **Gross Winning Trades Sum**: $+\$47.1179$
- **Gross Losing Trades Sum**: $-\$49.4556$
- **Gross Profit Factor**:
  $$\text{Gross PF} = \frac{47.1179}{49.4556} = \mathbf{0.9527}$$

- **Total Entry Fees on Closed Trades**: $\$2.0990$
- **Total Exit Fees on Closed Trades**: $\$2.0623$
- **Total Fees on Closed Trades**: $\$4.1613$
- **Net Winning Trades Sum** (after fees): $+\$45.8807$
- **Net Losing Trades Sum** (after fees): $-\$52.3797$
- **Net Profit Factor**:
  $$\text{Net PF} = \frac{45.8807}{52.3797} = \mathbf{0.8759} \quad (\approx \mathbf{0.88})$$

The fee drag of $\$4.1613$ on $\$96.57$ of gross turnover accounts for the reduction from Gross PF 0.95 to Net PF 0.88.

#### B. Penny-Perfect Reconciliation Identity
At the termination of the 425-bar run, 4 positions remained open:
1. `SUIUSDT`: Entry Notional $\$23.4735 \times 0.0010 = \$0.0235$
2. `BNBUSDT`: Entry Notional $\$117.8460 \times 0.0010 = \$0.1178$
3. `DOGEUSDT`: Entry Notional $\$116.3150 \times 0.0010 = \$0.1163$
4. `NEARUSDT`: Entry Notional $\$112.1904 \times 0.0010 = \$0.1122$
- **Sum of Open Positions Entry Fees**:
  $$\sum \text{Open Entry Fees} = 0.0235 + 0.1178 + 0.1163 + 0.1122 = \mathbf{\$0.3698}$$

- **Total Closed Trades Net PnL**:
  $$\sum \text{Net PnL} = 45.8807 - 52.3797 = -\mathbf{\$6.4990}$$

- **Reconciliation Calculation**:
  $$\text{Initial Capital} + \sum \text{Net PnL} - \sum \text{Open Entry Fees} = 300.0000 - 6.4990 - 0.3698 = \mathbf{\$293.1312}$$
- **Engine Reported `realized_equity`**: **\$293.1312**
- **Discrepancy**: **\$0.0000 (Penny-Perfect Match)**

This proves that zero money is leaked, omitted, or double-counted.

---

## 6. Historical Data Gate Verdict

| Gate Condition | Required Specification | Achieved Metric | Status |
| :--- | :--- | :--- | :---: |
| **Data Integrity** | 18 Parquet files, 0 gaps, 0 nulls | 18 verified, 0 gaps, 0 nulls | **PASS** |
| **Zero Lookahead** | Point-in-time sequential processing | $t \to t+1$ causal execution | **PASS** |
| **Listing Accuracy** | RENDER absent prior to index 4,976 | 0 bars prior to 2024-07-26 08:00 UTC | **PASS** |
| **Parity Invariant** | Signal diff across consecutive real bars | 0 SIGNAL_CHANGED divergences | **PASS** |
| **Synthetic Fallback** | Prohibited in production tests | Explicit `DATA_GATE_BLOCKED` failure | **PASS** |
| **Accounting Conservation** | Penny-perfect equity conservation | Identity holds to $\$0.0000$ | **PASS** |

**OVERALL DATA GATE VERDICT**: **APPROVED FOR IN-SAMPLE SIMULATION**
