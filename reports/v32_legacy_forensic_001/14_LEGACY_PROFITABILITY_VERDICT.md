# Final Forensic Audit Verdict: Bybit Multi-Alpha v3.2
## Audit Deliverable 14 — V32-LEGACY-FORENSIC-001

---

## 1. Core Audit Findings & Answers to the 5 Mandatory Questions

### Question 1: What specific version of code is running on port 8083?
- **Finding:** The daemon running on VPS `150.241.98.70:8083` is the **original Bybit Multi-Alpha v3.2 Dual-Regime server** (`v3_2/web_dashboard.py`).
- **Evidence:** The live HTML served over port 8083 matches local `v3_2/web_dashboard.py` bit-for-bit (53,166 bytes). Persistent state matches `v3_2.src.paper_trader_v32.PaperTraderV32`.
- **Verdict:** `CONFIRMED_ORIGINAL_V32_RUNTIME`.

### Question 2: How was the claimed +$25.76 actually generated?
- **Finding:** The claimed +$25.76 was generated **100% by the 1h Donchian Breakout Trend Engine (`CoreV3Engine`) trading SHORT positions on dynamic altcoins**.
- **Crucial Discovery:** The Range Mean-Reversion Maker Engine generated **$0.00** because an artificial OHLC candle bug (`open=high=low=close`) made limit fills mathematically impossible.
- **Accounting Breakdown:**
  - Realized Closed Trades Net PnL: **+$17.99**
  - Realized Partial TPs on active runners: **+$4.01**
  - Active Entry Fees Debited: **-$0.12**
  - **Realized Capital ("Капитал"):** **$321.88 (+ $21.88 Net PnL)**
  - Floating Mark-to-Market PnL on 3 Open Positions: **+$3.88**
  - **Current Equity ("Текущая Equity"):** **$325.76 (+ $25.76 Net PnL)**
- **Verdict:** `EVIDENCE_PASS` (Penny-perfect reconciliation confirmed).

### Question 3: What is the confirmed profit under realistic execution?
- **Scenario A (Dashboard Reported):** **+$21.88** Realized / **+$25.76** Total Equity
- **Scenario B (Closed Trades Only):** **+$17.99** (PF: 2.47, WR: 57.1%)
- **Scenario C (Realistic Slippage 0.05%):** **+$16.71**
- **Scenario D (Stress Test 0.10% + Top Win Excluded):** **+$11.08** (PF: 1.72)
- **Verdict:** `EVIDENCE_PASS` (Strategy is genuinely profitable across all stress scenarios).

### Question 4: What errors in the original code inflated or deflated results?
- **Deflating Defect:** The Range Maker module was completely paralyzed by the `open=high=low=close` bug, suppressing all mean-reversion alpha.
- **Inflating Defect:** Fixed 0.02% slippage slightly understated altcoin breakout slippage by ~0.03% ($1.28 total drag).
- **Accounting Defect:** Zero double-counting. Standard Bybit VIP0 taker fees (0.100%) and slippage were properly debited.
- **Verdict:** `EVIDENCE_PASS`.

### Question 5: Is there sufficient evidence to continue research on this alpha?
- **Finding:** **YES.** The 1h Donchian Breakout with Dynamic Universe Screener and Half-Kelly sizing achieved a genuine **Profit Factor of 2.47** and **57.1% Win Rate** during a 12-day live paper test on Bybit V5 feeds.
- **Verdict:** `RECOMMENDED_FOR_FURTHER_RESEARCH` (Research Only / No Live Orders yet).
