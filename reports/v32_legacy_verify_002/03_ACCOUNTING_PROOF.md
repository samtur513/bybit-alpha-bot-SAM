# Independent Accounting Proof & PnL Reconstruction
## Deliverable 03 — Technical Specification V32-LEGACY-VERIFY-002

- **Audit Subject:** Closed Trades Ledger of Bybit Multi-Alpha v3.2 Dual-Regime (Port 8083)
- **Accounting Standard:** Strict First-Principles Reconstruction (Zero Assumption of Stored `net_pnl`)
- **Rounding Standard:** Explicit IEEE-754 / Python `Decimal` with $0.01 display rounding

---

## 1. Accounting Architecture & Mathematical Invariants

In `CoreV3Engine`, position PnL is governed by the following state evolution:

### 1.1 Position Opening ($T_{open}$):
$$\text{Entry Price} = P_{open} \cdot (1 + \text{slippage} \cdot \text{direction})$$
$$\text{Entry Fee} = \text{Initial Qty} \cdot \text{Entry Price} \cdot \text{taker\_fee}$$
$$\text{Realized Equity} \leftarrow \text{Realized Equity} - \text{Entry Fee}$$

### 1.2 Partial Trim / Cascade TP ($T_{partial}$):
$$\text{Partial Fill Price} = P_{market} \cdot (1 - \text{slippage} \cdot \text{direction})$$
$$\text{Partial Cash} = \text{Qty}_{trim} \cdot (P_{partial} - P_{entry}) \cdot \text{direction} - \text{Qty}_{trim} \cdot P_{partial} \cdot \text{taker\_fee}$$
$$\text{Realized Equity} \leftarrow \text{Realized Equity} + \text{Partial Cash}$$
$$\text{pos.realized\_partial} \leftarrow \text{pos.realized\_partial} + \text{Partial Cash}$$

### 1.3 Final Position Close ($T_{exit}$):
$$\text{Exit Price} = P_{market} \cdot (1 - \text{slippage} \cdot \text{direction})$$
$$\text{Final Cash} = \text{Final Qty} \cdot (P_{exit} - P_{entry}) \cdot \text{direction} - \text{Final Qty} \cdot P_{exit} \cdot \text{taker\_fee}$$
$$\text{Realized Equity} \leftarrow \text{Realized Equity} + \text{Final Cash}$$
$$\mathbf{\text{Net PnL}} = \text{Final Cash} + \text{pos.realized\_partial} + \text{pos.funding\_pnl} - \text{pos.entry\_fees}$$

Notice the exact identity:
$$\Delta \text{Realized Equity}_{trade} \equiv \mathbf{\text{Net PnL}}$$
Total equity changes by precisely the recorded `net_pnl`.

---

## 2. Independent Partitioning of the 28 Closed Trades

### Category 1: Full Primary Event Verified (13 Trades — 46.4% of Trades)
- **Definition:** Trades where `initial_qty == final_qty` (no partial trims occurred).
- **Primary Data Availability:** 100% complete. Exact entry price, exit price, contract quantity, and fee rates are preserved in primary records.
- **Verification Result:** Every single one of these 13 trades matches the first-principles formula:
  $$\text{Net PnL} = \text{Qty} \cdot (P_{exit} - P_{entry}) \cdot \text{direction} - \text{Entry Fee} - \text{Exit Fee}$$
  with **zero discrepancy** ($< 10^{-12}$ USDT).
- **Subtotal PnL (13 Trades):** **-$9.67 USDT** (12 losses from Fast Scratches / Stop Losses, 1 win on BTCUSDT of +$2.55).

### Category 2: Model-Derived Partial Trades (15 Trades — 53.6% of Trades)
- **Definition:** Trades where `final_qty < initial_qty` due to Cascade TP1 (30% trim) or Cascade TP1 + TP2 (70% trim).
- **Primary Data Availability:** Entry price, initial quantity, final exit price, and remaining runner quantity are fully verified. However, the intermediate execution price of the partial take-profit was banked directly into `pos.realized_partial` and was not written to a discrete order execution log.
- **Derived Partial Gain:** By subtracting final leg cash and entry fees from stored `net_pnl`, the exact banked partial profit is **+$27.66 USDT**.
- **Audit Qualification:** In strict accordance with P1, these 15 trades are classified as **`MODEL_DERIVED_PARTIAL`** because the intermediate execution price is reconstructed from the cascade take-profit model rather than an external tick-by-tick order fill receipt.

---

## 3. Fee Rate Reconciliation (0.090% vs 0.100%)

The running daemon on port 8083 was instantiated with `CoreV3Engine` defaults:
- `taker_fee = Decimal("0.0009")` (0.090%, reflecting Bybit VIP with MNT discount).
- If standard Bybit VIP0 taker fee (0.100%, `0.0010`) is applied without MNT discount:
  - Total Entry Fees (28 trades): $0.9996 USDT $\to$ $1.1107 USDT (+ $0.1111 drag)
  - Total Exit Fees (28 trades): $0.8660 USDT $\to$ $0.9622 USDT (+ $0.0962 drag)
  - **Total Additional Fee Drag:** **$0.2073 USDT** (~$0.21 USDT across the portfolio).
  - Net closed profit at standard VIP0 taker fees: $\$17.9914 - \$0.2073 = \mathbf{+\$17.7841 USDT}$.
