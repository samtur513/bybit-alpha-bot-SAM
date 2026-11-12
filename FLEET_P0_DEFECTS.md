# FLEET_P0_DEFECTS.md: Forensic Fleet-Wide P0 Defect Catalog

**Audit Date**: November 12, 2026  
**Audit Protocol**: FLEET-REBOOT-001 (Step 1 Defect Discovery)  
**Evaluator**: Lead Quant Engineer & Independent Financial Auditor  
**Scope**: Entire Bybit Quantitative Trading Fleet (Ports 8080–8098)  

---

## Executive Defect Summary

The independent forensic audit of the Sovereign Quantum Fleet revealed four systemic defect clusters that distort paper trading metrics, understate trading frictions, and risk portfolio solvency:
1. **Fleet-Wide Hardcoded MNT Fee Bias**: 16 out of 18 systems hardcode a 10% fee discount (Taker 0.090%, Maker 0.0324%) that is unverified for automated API trading accounts.
2. **Double-Entry Accounting Decoupling & Phantom Profit**: Systems with partial take-profits (e.g. `v3_5`, `v3_2`, `v3_8`, `v3_14`) credit equity in memory without recording slice events in persistent trade logs, causing internal accounting discrepancies up to **+$661.54**.
3. **Broken-Leg Unhedged Beta Risk in Multi-Leg Systems**: Pair and basket engines (`v3_13_sector_arb`, `v3_14_basket_dispersion`, `v3_12_crossbasis`) execute legs asynchronously without atomic rollback or timeout hedging, exposing the portfolio to 100% directional market risk upon partial leg failure.
4. **Fictitious Maker Fill Assumptions**: High-frequency and scalping modules (`v3_7`, `v3_9`, `v3_7_v4_exp`, `v3_10_sweep`) log limit orders as 100% filled (`is_maker=True`) without modeling queue priority, front-running, or adverse selection.

---

## 1. DATA & TIME INVARIANTS (P0-DT)

### P0-DT-01: Hardcoded MNT Fee Discount Bias across Fleet
- **Affected Systems**: `v3_1_4`, `v3_2`, `v3_3`, `v3_4`, `v3_6`, `v3_7`, `v3_8`, `v3_9`, `v3_10_sweep`, `v3_11_funding`, `v3_12_crossbasis`, `v3_13_sector_arb`, `v3_14_basket_dispersion`.
- **Manifestation**:
  In configs and engine files:
  ```python
  BASE_TAKER_FEE = Decimal("0.0009")  # 0.09% (with MNT 10% discount)
  FEE_MAKER = 0.000324                # 0.0324%
  ```
  And in `funding_8097.py` and `crossbasis_8096.py`:
  ```python
  self.fee_taker = 0.00090 if os.getenv("USE_MNT_DISCOUNT", "true").lower() == "true" else 0.00100
  ```
- **Microstructural Reality**: Bybit VIP0 accounts without active MNT deduction in UTA pay standard rates: **0.1000% Taker / 0.0360% Maker**.
- **Financial Impact**: Understates total trading fee burn by **11.1%** on every single fill, inflating strategy net returns and masking fee drag.
- **Mandatory Remediation**: Enforce standard conservative profile (Taker 0.1000%, Maker 0.0360%) across all engines for unconfirmed accounts.

### P0-DT-02: Intra-Bar Execution with Lookahead on Incomplete Candles
- **Affected Systems**: `v3_2` (Port 8083), `v3_6` (Port 8089).
- **Manifestation**: The engine processes real-time WebSocket ticks and triggers breakout entries when the current live tick exceeds the Donchian Upper channel of the *currently forming* candle, rather than awaiting official bar close $t-1$.
- **Financial Impact**: Lookahead bias; entering on intra-bar wicks that subsequent price action retraces, generating false breakouts that would never appear on closed-candle charts.
- **Mandatory Remediation**: Enforce `shift(1)` on closed bars for signal generation. Bar $t$ must close before bar $t+1$ executes.

### P0-DT-03: Multi-Leg Timestamp Desynchronization
- **Affected Systems**: `v3_13_sector_arb` (Port 8080), `v3_14_basket_dispersion` (Port 8094).
- **Manifestation**: Spread and dispersion calculations evaluate Leg A and Leg B using whichever quote arrived last, without verifying quote freshness or synchronized arrival timestamps.
- **Financial Impact**: Calculating spread across quotes with a 500ms–2000ms timestamp gap creates phantom arbitrage spreads that vanish upon simultaneous order submission.

---

## 2. EXECUTION & MICROSTRUCTURE INVARIANTS (P0-EX)

### P0-EX-01: Fictitious Maker Fill Assumption (`is_maker=True`)
- **Affected Systems**: `v3_10_sweep` (Port 8098), `v3_7` (Port 8090), `v3_8` (Port 8091), `v3_11_funding` (Port 8097), `v3_13_sector_arb` (Port 8080).
- **Manifestation**:
  In `v3_13_sector_arb/src/sector_arb_8080.py` line 748:
  ```python
  self.ledger.record_closed_trade(..., is_maker=True, fee_usdt=-trade_total_fees)
  ```
  And in `v3_8/src/core_v38.py`: Stage 2 Take-Profit (+1.40R) assumes an immediate 100% fill at limit price without modeling orderbook depth or queue time.
- **Microstructural Reality**: Limit orders posted at the top of the book are subject to queue priority. In sharp momentum moves, limit orders are either left unfilled or suffer toxic adverse selection fills.
- **Financial Impact**: In the fleet snapshot, `v3_7` and `v3_9` recorded **0.0% Win Rate** because in live reality, limit orders rarely captured maker rebate on exit and took full taker fee on stop losses.

### P0-EX-02: Broken Pair / Basket Leg Execution Isolation
- **Affected Systems**: `v3_13_sector_arb` (Port 8080), `v3_14_basket_dispersion` (Port 8094), `v3_12_crossbasis` (Port 8096).
- **Manifestation**:
  When submitting multi-leg orders:
  - Leg 1 is sent via Market/Limit.
  - If Leg 2 rejects (due to MinNotional, price quantization, or connection drop), Leg 1 remains open indefinitely with no automatic rollback or emergency hedge.
- **Risk Impact**: Transforms a purported beta-neutral pair (e.g. SOL/SUI) into a naked 100% directional position, subjecting the strategy to unhedged market crash risk.
- **Mandatory Remediation**: Implement an Atomic Two-Leg Execution Gate with automatic unhedged timeout ejection.

---

## 3. ACCOUNTING & LEDGER INVARIANTS (P0-AC)

### P0-AC-01: Partial Take-Profit Decoupling from Closed Trades Ledger
- **Affected Systems**: `v3_5` (Port 8088), `v3_2` (Port 8083), `v3_14` (Port 8094).
- **Manifestation**:
  When a position achieves TP1 (+4.0R, 50% trim):
  `self.realized_equity += tp_net` is executed, but no closed trade slice is appended to `self.closed_trades`.
- **Financial Impact**:
  - `realized_equity` accumulates cash gains while `closed_trades` shows net losses.
  - In `v3_5`, this created a **+$661.54** accounting gap.
  - In `v3_2` and `v3_14`, metrics on the web dashboard display divergent win rates and profit factors.
- **Mandatory Remediation**: Every partial close must generate an immutable, unique slice event in the ledger with exact pro-rata entry fee allocation.

### P0-AC-02: Funding Cashflow Omission in Directional Trend Engines
- **Affected Systems**: `v3_1_4` (Port 8084), `v3_3` (Port 8086), `v3_4` (Port 8087), `v3_8` (Port 8091), `v3_v4_exp` (Port 8082).
- **Manifestation**: Positions held across multiple 8-hour funding intervals (often 24 to 72 hours) never calculate or deduct funding payments from realized equity.
- **Financial Impact**: In sustained bull markets, holding short altcoin hedges incurs heavy funding drag (up to 0.03% – 0.09% daily). Omitting funding inflates reported net performance by 5% – 15% annually.

### P0-AC-03: Pyramiding Average Entry Price Distortion
- **Affected Systems**: `v3_1_4` (Port 8084), `v3` Production Baseline.
- **Manifestation**: When pyramiding into a winning trend position (adding slices at +1.0R and +2.0R), the engine calculates subsequent stop-loss and trailing profit against the *initial* entry price rather than the *volume-weighted average entry price (VWAEP)*.
- **Financial Impact**: Distorts R-multiple accounting and can cause a pyramided trade that drops back to breakeven of the first slice to register as a net loss while the system reports a win.

---

## 4. RISK MANAGEMENT & KILL-SWITCH INVARIANTS (P0-RM)

### P0-RM-01: Permanent Unconditional Trading Halt on Drawdown Limit
- **Affected Systems**: `v3_5` (Port 8088), `v3_1_4` (Port 8084), `v3_3` (Port 8086), `v3_4` (Port 8087).
- **Manifestation**:
  When equity drawdown crosses `DRAWDOWN_LIMIT_PCT` (e.g. 15.0%):
  ```python
  self.trading_halted = True
  ```
  There is no automated hysteresis reset, cooling-off window, or recovery path. The daemon halts trading permanently and never resumes unless manually restarted.
- **Operational Impact**: In the forensic backtest of `v3_5`, `REPAIRED` halted permanently at bar 1,097 (Month 2), leaving the remaining 16 months untested until `shadow_mode` was engineered.

### P0-RM-02: Time-Stop Over-Churning in Low Volatility
- **Affected Systems**: `v3_13_sector_arb` (Port 8080), `v3_9` (Port 8092).
- **Manifestation**: Enforces a rigid time-stop (e.g. `TIME_STOP_1200S = 20 minutes`). If the statistical spread has not fully converged in 20 minutes, the trade is liquidated at market.
- **Financial Impact**: In the fleet snapshot for Port 8080, **33 trades were executed with a Win Rate of only 6.1%** because positions were chopped out by the 20-minute timer before mean reversion could occur, burning 0.18% round-trip fees on every attempt.

---

## Defect Triage & Impact Classification Matrix

| Defect ID | Category | Defect Title | Impact Severity | Remediation Complexity | Mandatory for Paper V2? |
| :--- | :--- | :--- | :---: | :---: | :---: |
| **P0-DT-01** | Data/Time | Hardcoded 10% MNT Fee Discount | **CRITICAL** | Low (Config update) | **YES** |
| **P0-DT-02** | Data/Time | Intra-Bar Execution with Lookahead | **CRITICAL** | Medium (Shift bar index) | **YES** |
| **P0-DT-03** | Data/Time | Multi-Leg Quote Desynchronization | **HIGH** | Medium (Sync check) | **YES** |
| **P0-EX-01** | Execution | Fictitious Maker Fill Assumptions | **CRITICAL** | Medium (Fill adapter) | **YES** |
| **P0-EX-02** | Execution | Broken Multi-Leg Unhedged Beta Risk | **CRITICAL** | High (Atomic gate) | **YES** |
| **P0-AC-01** | Accounting | Partial TP Decoupling from Ledger | **CRITICAL** | Medium (Slice logging) | **YES** |
| **P0-AC-02** | Accounting | Funding Omission on Multi-Day Holds | **HIGH** | Medium (8h settlement) | **YES** |
| **P0-AC-03** | Accounting | Pyramiding Average Entry Price Bias | **HIGH** | Medium (VWAEP formula) | **YES** |
| **P0-RM-01** | Risk | Permanent Unconditional Halt Deadlock| **MEDIUM** | Low (Shadow diagnostics)| **YES** |
| **P0-RM-02** | Risk | Rigid Time-Stop Machine-Gun Churn | **HIGH** | Low (Extend/dynamic timer)| **YES** |
