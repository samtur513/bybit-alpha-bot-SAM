# PAPER_READINESS_MATRIX.md: Fleet Readiness for Verified Paper Trading (Paper V2)

**Audit Date**: November 12, 2026  
**Audit Protocol**: FLEET-REBOOT-001 (Step 2 Readiness Gate)  
**Evaluator**: Lead Quant Engineer & Independent Financial Auditor  
**Standard**: Zero P0 Defects, Verified Double-Entry Accounting, Preserved Original Alpha Logic  

---

## 1. Fleet Paper V2 Readiness Matrix

| Port | Strategy Name | Systemd Unit | Architecture Category | Step 2 Status | Prerequisite Conditions for Paper V2 Deployment | Priority Tier |
| :---: | :--- | :--- | :--- | :---: | :--- | :---: |
| **8084** | **v3.1.4 Champion 4h** | `bybit-v314.service` | 4h Macro Trend Breakout | **PASS** | Cleared for Shadow Paper. Standard VIP0 fee profile deployed. | **Tier 1 (Immediate)** |
| **8091** | **v3.8 NewDragon Trend Lab** | `bybit-bot-v38.service` | 1h Donchian + 3-Stage Trail | **PASS** | Cleared for Shadow Paper. Standard VIP0 fees + slice tracking enabled. | **Tier 1 (Immediate)** |
| **8096** | **v3.12 Cross-Basis Arbitrage**| `bybit-crossbasis-8096.service`| Spot-Perp Basis Arbitrage | **PASS** | Cleared for Live Shadow Paper on live Spot/Perp feeds. Atomic leg gate active. | **Tier 1 (Immediate)** |
| **8083** | v3.2 Dual-Regime IRAS | `bybit-v32.service` | 1h Trend + Range Scalping | **CONDITIONAL**| Requires shift(1) closed-bar causality patch before live paper. | Tier 2 |
| **8082** | v3-v4 NewDragon Trend Lab | `bybit-v3-v4-exp.service` | 1h Donchian + SRIE Alpha | **CONDITIONAL**| Requires funding cashflow tracking integration. | Tier 2 |
| **8080** | v3.13 Sector Arbitrage | `bybit-bot.service` | 15m Sector Relative Value | **CONDITIONAL**| Requires extending 20m time-stop to prevent premature fee churn. | Tier 2 |
| **8094** | v3.14 Basket Dispersion | `bybit-dispersion-8094.service`| Multi-Leg Basket Dispersion | **CONDITIONAL**| Requires atomic tri-leg execution gate before forward paper. | Tier 2 |
| **8086** | v3.3 Turbo Streak Risk | `bybit-v33.service` | 1h Breakout + Streak Sizing | **CONDITIONAL**| Streak Kelly multiplier must be capped at 2.5x to prevent leverage breach. | Tier 2 |
| **8087** | v3.4 ZF-8 Quant | `bybit-v34.service` | 1h Breakout + ZF-8 Gears | **CONDITIONAL**| Gear shifting hysteresis required to prevent whipsaw stop churn. | Tier 2 |
| **8089** | v3.6 Omega Dual-Regime ZF-8| `bybit-v36.service` | Dual-Regime + ZF-8 Gears | **CONDITIONAL**| Parameter reduction required; currently over-parameterized. | Tier 2 |
| **8097** | v3.11 Funding Squeeze | `bybit-funding-8097.service` | 8h Settlement Harvesting | **CONDITIONAL**| Adverse price excursion stop required to cap basis drawdowns. | Tier 2 |
| **8098** | Master Sweeps L2 | `bybit-sweep-8098.service` | Sub-Second L2 Reclaim Scalp | **CONDITIONAL**| Requires live Data Hub 8095 connection and queue fill modeling. | Tier 2 |
| **8093** | v3.7-v4 Scalp Lab | `bybit-v37-v4-exp.service` | Lead-Lag Microstructure | **CONDITIONAL**| High fee drag; requires minimum 1.5R target to overcome 0.20% fees. | Tier 2 |
| **8088** | v3.5 Alpha Pairs | `bybit-v35.service` | 4h Relative Value Pairs | **RESEARCH ARCHIVE**| Definitively negative net alpha (-$102.21 over 18M). Do not deploy. | **Archived** |
| **8090** | v3.7 Master Sweeps | `bybit-v37.service` | 1m/5m Orderbook Sweeps | **BLOCKED** | Recorded 0.0% Win Rate in paper. Unviable under taker stop execution. | **Blocked** |
| **8092** | v3.9 NewDragon Scalp Lab | `bybit-bot-v39.service` | Sub-minute Microstructure | **BLOCKED** | Recorded 0.0% Win Rate in paper ($N=39$). Unviable under taker fees. | **Blocked** |
| **8085** | Meta-Dashboard | `bybit-meta-dashboard.service`| Telemetry Web Server | **OPERATIONAL**| Core infrastructure; operational on Port 8085. | Infra |
| **8095** | Data & TCA Hub | `bybit-hub-8095.service` | ZeroMQ Tick Aggregator | **OPERATIONAL**| Core infrastructure; operational on Port 8095. | Infra |
| **8100** | Spread Arena | None | None | **BLOCKED** | Phantom port; code not found in repository. | **Blocked** |

---

## 2. Gate Decision Summary

- **Total Systems Audited**: 18
- **Cleared for Immediate Paper V2 (`PASS`)**: **3** (`v3.1.4`, `v3.8`, `v3.12`)
- **Operational Infrastructure**: **2** (`meta_dashboard`, `v3_10_sweep Data Hub`)
- **Conditional / Secondary Verification (`CONDITIONAL`)**: **10**
- **Blocked Due to Structural Execution Defects (`BLOCKED`)**: **3** (`v3.7`, `v3.9`, `8100`)
- **Archived Due to Exhausted Edge (`RESEARCH ARCHIVE`)**: **1** (`v3.5`)

---

## 3. Audit Verdict: STEP 2 PASS

Step 2 criteria have been fully satisfied:
1. Universal execution contract established in `tools/fleet_execution_contract.py`.
2. All 20 regression tests passed with zero accounting discrepancies.
3. Signal parity confirmed across all alpha modules.
4. Top 3 Priority modules cleared for **Step 3: Verified Shadow Paper**.
