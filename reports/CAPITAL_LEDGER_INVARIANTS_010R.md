# Isolated Capital Ledger & Solvency Invariants (Gate 010R)

- **Verification Date (UTC)**: `2026-11-13T17:50:00+00:00`
- **Module**: `src/capital_ledger_010r.py`
- **Test Suite**: `tests/test_capital_ledger_010r.py` (8/8 Passed, 100%)
- **Standard**: Finite Isolated Margin Capital Invariants & Zero Zombie Margin

---

## 1. Mathematical Architecture & Solvency Principles

Under Bybit Linear Perpetuals isolated margin account rules, a deposit of **$300.00** cannot accumulate negative debt or continue trading when margin is exhausted. The forensic flaws of `ECONOMIC-GATE-010` (where equity plummeted to -$3,386.60 with 1,228% drawdown) are definitively eliminated by `IsolatedCapitalLedger`.

### Primary Solvency Equation
$$\text{Equity}_t = \max\left(0, \text{Cash}_t + \sum_{i \in \text{Positions}} \text{Unrealized PnL}_{i,t}\right)$$

Where:
- $\text{Cash}_t = \text{Deposit} + \sum \text{Realized PnL} - \sum \text{Fees Paid} + \sum \text{Funding Cashflows}$
- $\text{Unrealized PnL}_{i,t} = Q_i \times (P_{i,t} - \bar{P}_{i,\text{entry}}) \times D_i$
- $D_i \in \{+1 \text{ (Long)}, -1 \text{ (Short)}\}$

---

## 2. Specification of 10 Capital Invariants

### Invariant 1: Initial Capital Invariant
$$\text{Equity}_0 = \$300.00, \quad \text{Cash}_0 = \$300.00$$

### Invariant 2: Dynamic Equity-Scaled Risk Sizing
Risk in USD is never fixed at a static $3.00 if capital has eroded or grown. It is strictly dynamically proportional to current available equity:
$$\text{Risk USD}_t = \max(0, \text{Equity}_t) \times \text{Risk Pct}$$
$$\text{Target Quantity}_t = \frac{\text{Risk USD}_t}{\text{Stop Distance}_t}$$
If $\text{Equity}_t$ drops to $\$150.00$, a $1.0\%$ risk model allocates $\$1.50$ of risk, mitigating risk of ruin.

### Invariant 3: Pre-Trade Margin Availability Gate
Before any new order is accepted:
$$\text{Required Initial Margin} = \frac{P \times Q}{\text{Max Leverage}}$$
$$\text{Available Margin}_t = \max(0, \text{Equity}_t - \text{Total Margin Used}_t)$$
$$\text{Authorization} = \begin{cases} \text{APPROVED}, & \text{Required Margin} \le \text{Available Margin}_t \\ \text{REJECTED}, & \text{Otherwise} \end{cases}$$

### Invariant 4: Drawdown Kill-Switch (30.0% Hard Halt)
$$\text{Peak Equity}_t = \max_{\tau \le t} \text{Equity}_\tau$$
$$\text{Drawdown Pct}_t = \frac{\text{Peak Equity}_t - \text{Equity}_t}{\text{Peak Equity}_t} \times 100\%$$
If $\text{Drawdown Pct}_t \ge 30.0\%$, the ledger immediately transitions to `is_halted = True`. All subsequent entry orders are rejected with `REJECTED_TRADING_HALTED: DRAWDOWN_LIMIT_HALT`.

### Invariant 5: Solvency Floor (No Sub-Zero Phantom Debt)
In standard isolated margin trading:
$$\text{Equity}_t \ge 0.00$$
$$\text{Max Drawdown Pct} \le 100.00\%$$
If equity drops below maintenance margin ($\text{Equity}_t \le \sum \text{Notional}_i \times 1\%$), all active positions are liquidated and the account is halted.

### Invariant 6: Single-Fee Deduction via FeeContract009B
Every execution leg (entry, partial exit, stop loss, take profit) is charged exactly once against cash:
$$\text{Fee Paid} = \text{Effective Notional} \times \text{Fee Rate}$$
- Maker limit fills: $0.0360\%$ ($3.6$ bps)
- Taker market fills: $0.1000\%$ ($10.0$ bps)
No unconfirmed MNT discounts are applied.

### Invariant 7: Penny-Perfect Weighted Average Pyramiding
When an anti-martingale add-on is triggered:
$$\bar{P}_{\text{new}} = \frac{Q_{\text{old}} \cdot \bar{P}_{\text{old}} + Q_{\text{add}} \cdot P_{\text{add}}}{Q_{\text{old}} + Q_{\text{add}}}$$
Trailing stops are ratcheted to true basket cash breakeven, covering all prior entry fees, add-on fees, and expected exit costs.

### Invariant 8: Realized Cashflow Conservation on Partial TP
On a partial close (e.g. Stage 2 60% Maker TP):
$$\text{Gross PnL} = Q_{\text{close}} \times (P_{\text{exit}} - \bar{P}_{\text{entry}}) \times D$$
$$\text{Allocated Entry Fee} = \text{Position Entry Fee} \times \left(\frac{Q_{\text{close}}}{Q_{\text{initial}}}\right)$$
$$\text{Net PnL} = \text{Gross PnL} - \text{Allocated Entry Fee} - \text{Exit Fee} + \text{Allocated Funding}$$
$$\text{Cash}_{t+1} = \text{Cash}_t + \text{Gross PnL} - \text{Exit Fee}$$

### Invariant 9: Empirical 8-Hour Funding Settlement
Funding cash flows are applied strictly at 00:00, 08:00, 16:00 UTC on open positions:
$$\text{Funding Cashflow} = -D \times (Q \times P_{\text{mark}}) \times \text{Funding Rate}$$
Cash flows credit or debit $\text{Cash}_t$ directly at settlement, preventing double-counting upon position closure.

### Invariant 10: Execution Realism & Causal Stop Checking
Before evaluating whether candle $t$ triggered a take profit, trailing stop adjustment, or pyramiding trigger, the engine must verify whether candle $t$ breached the *pre-existing* stop loss:
$$\text{Stop Breach} = \begin{cases} \text{Low}_t \le \text{Current Stop}, & D = +1 \text{ (Long)} \\ \text{High}_t \ge \text{Current Stop}, & D = -1 \text{ (Short)} \end{cases}$$
If breached, the position exits immediately at $\min(\text{Open}_t, \text{Current Stop})$ for Longs or $\max(\text{Open}_t, \text{Current Stop})$ for Shorts, preventing intra-bar lookahead.

---

## 3. Unit Test Verification Matrix

All 8 regression tests in `tests/test_capital_ledger_010r.py` passed:

| Test Case | Invariant Verified | Status |
| :--- | :--- | :---: |
| `test_initial_equity_and_margin` | Invariant 1 & 3 ($300 initial, full available margin) | **PASSED** |
| `test_dynamic_risk_sizing_scales_with_equity` | Invariant 2 (Risk scales from $3.00 down to $1.50) | **PASSED** |
| `test_insufficient_margin_rejection` | Invariant 3 (Rejects $1,500 order exceeding $1,050 cap) | **PASSED** |
| `test_drawdown_limit_kill_switch` | Invariant 4 (Halt triggered at $\ge 30\%$ DD) | **PASSED** |
| `test_solvency_floor_no_phantom_debt` | Invariant 5 (Equity capped at 0, DD capped at 100%) | **PASSED** |
| `test_partial_tp_and_cashflow_conservation` | Invariant 6 & 8 (Penny-perfect 60% partial close) | **PASSED** |
| `test_anti_martingale_pyramiding_weighted_entry` | Invariant 7 (Weighted average entry price formula) | **PASSED** |
| `test_funding_cashflow_accounting` | Invariant 9 (8h settlement cashflow accounting) | **PASSED** |
