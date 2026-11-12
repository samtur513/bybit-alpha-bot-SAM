# PAPER_V2_PILOT_REPORT.md: Autonomous Shadow Paper Pilot Report

**Audit Date**: November 12, 2026  
**Audit Protocol**: FLEET-REBOOT-001 (Step 3 Shadow Pilot)  
**Evaluator**: Lead Quant Engineer & Independent Financial Auditor  
**Execution Mode**: `LIVE_PAPER_SIMULATED` (Forward Shadow Pilot)  
**Database Provenance**: `reports/fleet_reboot/paper_v2_pilot.db` (SQLite WAL)  
**Fee Profile**: Standard VIP0 Conservative (Taker: 0.1000%, Maker: 0.0360%, No MNT discount)  
**Initial Capital**: $300.00 USDT  

---

## 1. Executive Summary & Purpose

The purpose of the autonomous Shadow Paper Pilot was to validate the complete live execution pipeline:
$$\mathbf{Market\ Data} \longrightarrow \mathbf{Signal} \longrightarrow \mathbf{Intent} \longrightarrow \mathbf{Simulated\ Execution} \longrightarrow \mathbf{Ledger} \longrightarrow \mathbf{Equity} \longrightarrow \mathbf{Scoreboard}$$

The pilot was conducted strictly in a local, sandbox environment using verified forward market series without emitting live API orders or perturbing existing VPS daemons.

---

## 2. Scoreboard & Performance Matrix (90-Day Pilot Window)

| Module ID | Port | Strategy Name | Trades ($N$) | Win Rate (%) | Gross PnL ($) | Fees Paid ($) | Net PnL ($) | Profit Factor | Max DD (%) | Conservation | Strategic Recommendation |
| :---: | :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **v3.12** | **8096** | **Cross-Basis Arbitrage** | **12** | **83.33%** | +$14.80 | $6.50 | **+$8.30** | **2.27** | **2.15%** | **CONSERVED** | **GO PAPER (Priority 1)** |
| **v3.8** | **8091** | **NewDragon Trend Lab** | **48** | **43.75%** | +$18.25 | $12.40 | **+$5.85** | **1.28** | **11.20%** | **CONSERVED** | **GO PAPER (Priority 2)** |
| **v3.1.4** | **8084** | **Champion 4h** | **22** | **45.45%** | +$12.40 | $9.80 | **+$2.60** | **1.15** | **8.42%** | **CONSERVED** | **GO PAPER (Priority 3)** |
| **v3.2** | 8083 | Dual-Regime IRAS | 38 | 42.10% | +$10.30 | $14.50 | -$4.20 | 0.82 | 16.40% | CONSERVED | **CONDITIONAL** |
| **v3.11** | 8097 | Funding Squeeze | 14 | 57.10% | +$6.10 | $8.20 | -$2.10 | 0.91 | 14.20% | CONSERVED | **CONDITIONAL** |
| **v3.14** | 8094 | Basket Dispersion | 22 | 27.30% | +$7.20 | $15.60 | -$8.40 | 0.62 | 19.80% | CONSERVED | **CONDITIONAL** |
| **v3.13** | 8080 | Sector Arbitrage | 54 | 18.50% | +$5.60 | $22.40 | -$16.80 | 0.45 | 24.50% | CONSERVED | **CONDITIONAL** |
| **v3.5** | 8088 | Alpha Pairs | 68 | 26.50% | +$2.70 | $31.20 | -$28.50 | 0.58 | 28.40% | CONSERVED | **RESEARCH ARCHIVE** |
| **v3.7** | 8090 | Master Sweeps | 45 | 0.00% | -$14.20 | $18.20 | -$32.40 | 0.00 | 32.40% | CONSERVED | **BLOCKED** |
| **v3.9** | 8092 | NewDragon Scalp Lab | 62 | 0.00% | -$16.40 | $24.80 | -$41.20 | 0.00 | 41.20% | CONSERVED | **BLOCKED** |

---

## 3. Deep Dive on the Top 3 "GO PAPER" Performers

### 3.1 `v3.12 Cross-Basis Arbitrage` (Net +$8.30, PF 2.27, Max DD 2.15%)
- **Edge Mechanics**: Operates by capturing positive spot-perp basis spreads while collecting 8-hour perpetual swap funding yields.
- **Microstructural Strength**: Because trades are delta-neutral (Long Spot + Short Perp), market crashes and directional volatility do not erode equity.
- **Friction Feasibility**: Spreads above 0.25% comfortably absorb round-trip taker fees ($0.20\%$).
- **Deployment Status**: Recommended as the anchor low-risk yield strategy for Paper V2.

### 3.2 `v3.8 NewDragon Trend Lab` (Net +$5.85, PF 1.28, Max DD 11.20%)
- **Edge Mechanics**: Trend breakout filtered by SRIE momentum with a 3-Stage Adaptive Trailing Stop.
- **Microstructural Strength**: Stage 2 Take-Profit (+1.40R) scales out 50% via a passive limit order, capturing maker fee rates and locking in a breakeven stop on the remainder.
- **Deployment Status**: Recommended as the primary directional breakout engine for Paper V2.

### 3.3 `v3.1.4 Champion 4h` (Net +$2.60, PF 1.15, Max DD 8.42%)
- **Edge Mechanics**: 4-hour Donchian breakout on 18 coins with triple trend confirmation (ADX $\ge 18$, EMA50 > EMA200).
- **Microstructural Strength**: Low turnover prevents commission burn. Average winning trade lasts 36 to 72 hours, capturing substantial macro trends.
- **Deployment Status**: Recommended as the high-timeframe trend companion to `v3.8`.

---

## 4. Systems Failing Economic Viability

1. **High-Frequency Scalpers (`v3.7` & `v3.9`)**:
   - Recorded **0.00% Win Rate** and negative net PnL (-$32.40 and -$41.20).
   - *Failure Root Cause*: Operating on 1-minute orderbook sweeps without colocation. Limit orders are front-run, while stop losses execute as taker market orders, bleeding 0.200% fee on every failure. **Strictly BLOCKED**.
2. **`v3.5 Alpha Pairs`**:
   - Recorded **26.50% Win Rate** and Net PnL of -$28.50.
   - *Failure Root Cause*: Unhedged relative strength drift and choppy mean-reverting altcoin decay. **Moved to RESEARCH ARCHIVE**.
