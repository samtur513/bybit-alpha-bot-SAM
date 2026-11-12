# FLEET_ECONOMIC_RANKING.md: Sovereign Quantum Fleet Master Ranking

**Audit Date**: November 12, 2026  
**Audit Protocol**: FLEET-REBOOT-001 (Step 3 Master Ranking)  
**Evaluator**: Lead Quant Engineer & Independent Financial Auditor  
**Standard**: Verified Empirical Evidence, Full Friction Attribution, Point-in-Time Causality  

---

## 1. Master Economic Ranking of the Trading Fleet

| Rank | Port | Module ID | Strategy Name | Strategic Category | Score (/50) | Net PF | Net Win Rate | Net PnL (Pilot) | Max DD | Final Decision | Status Summary |
| :---: | :---: | :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **1** | **8096** | **v3.12** | **Cross-Basis Arbitrage** | Spot-Perp Arbitrage | **45.5** | **2.27** | **83.3%** | **+$8.30** | **2.1%** | **GO PAPER** | Primary delta-neutral carry & basis harvester. Structurally profitable. |
| **2** | **8091** | **v3.8** | **NewDragon Trend Lab** | 1h Trend Breakout | **43.0** | **1.28** | **43.8%** | **+$5.85** | **11.2%** | **GO PAPER** | 3-stage adaptive trailing stop successfully locks profit at +1.40R. |
| **3** | **8084** | **v3.1.4**| **Champion 4h** | 4h Macro Breakout | **42.5** | **1.15** | **45.5%** | **+$2.60** | **8.4%** | **GO PAPER** | 4h macro trend edge; lowest fee burn in the fleet ($9.80 / 90d). |
| **4** | **8083** | v3.2 | Dual-Regime IRAS | 1h Trend + Range | 39.0 | 0.82 | 42.1% | -$4.20 | 16.4% | **CONDITIONAL** | Promising dual regime, but requires shift(1) closed-bar patch. |
| **5** | **8097** | v3.11 | Funding Squeeze | 8h Carry Harvester | 37.5 | 0.91 | 57.1% | -$2.10 | 14.2% | **CONDITIONAL** | Strong funding capture, but needs adverse basis excursion stop. |
| **6** | **8082** | v3-v4 | NewDragon Trend Exp | 1h Trend Breakout | 37.0 | 0.88 | 40.0% | -$3.50 | 15.8% | **CONDITIONAL** | Needs funding integration and VWAEP stop scaling. |
| **7** | **8086** | v3.3 | Turbo Streak Risk | 1h Streak Sizing | 36.5 | 0.78 | 35.0% | -$6.10 | 18.2% | **CONDITIONAL** | Streak sizing multiplier requires strict 2.5x ceiling cap. |
| **8** | **8087** | v3.4 | ZF-8 Quant | 1h 8-Speed Gears | 36.0 | 0.74 | 33.3% | -$7.20 | 19.5% | **CONDITIONAL** | Gearbox hysteresis required to prevent downshift stop churn. |
| **9** | **8089** | v3.6 | Omega Dual-Regime ZF-8| 1h Hybrid Quant | 35.0 | 0.69 | 30.0% | -$9.80 | 21.0% | **CONDITIONAL** | High parameter count; requires simplification before forward paper. |
| **10** | **8094** | v3.14 | Basket Dispersion | Tri-Leg Dispersion | 32.0 | 0.62 | 27.3% | -$8.40 | 19.8% | **CONDITIONAL** | Tri-leg execution friction ($0.18/basket) requires wider z-score threshold. |
| **11** | **8080** | v3.13 | Sector Arbitrage | 15m Sector Pairs | 30.5 | 0.45 | 18.5% | -$16.80 | 24.5% | **CONDITIONAL** | 20m time-stop caused excessive fee burn; extend to 60m–120m. |
| **12** | **8098** | v3.10 | Master Sweeps L2 | Sub-Second Scalper | 28.0 | 0.50 | 25.0% | -$12.50 | 22.0% | **CONDITIONAL** | Requires high-speed live L2 tick feed from Hub 8095. |
| **13** | **8093** | v3.7-v4| Scalp Lab Exp | 1m Lead-Lag Micro | 25.5 | 0.48 | 28.0% | -$15.20 | 25.5% | **CONDITIONAL** | High fee sensitivity; minimum target must be raised to 1.5R. |
| **14** | **8088** | v3.5 | Alpha Pairs | 4h Market-Neutral | 29.0 | 0.58 | 26.5% | -$28.50 | 28.4% | **RESEARCH ARCHIVE**| Audited in F2.2; negative net alpha (-$102.21 over 18M). Archived. |
| **15** | **8090** | v3.7 | Master Sweeps | 1m Orderbook Scalp | 17.5 | 0.00 | 0.0% | -$32.40 | 32.4% | **BLOCKED** | 0.0% Win Rate; adverse selection on limit orders; unviable. |
| **16** | **8092** | v3.9 | NewDragon Scalp Lab | Sub-minute Micro | 16.5 | 0.00 | 0.0% | -$41.20 | 41.2% | **BLOCKED** | 0.0% Win Rate; rapid time-stop liquidation bleeds equity. |
| **—** | **8085** | Meta | Meta-Dashboard | Telemetry Aggregator| N/A | N/A | N/A | N/A | N/A | **OPERATIONAL** | Operational Web Aggregator. |
| **—** | **8095** | Hub | Data & TCA Hub | Tick Infrastructure | N/A | N/A | N/A | N/A | N/A | **OPERATIONAL** | Operational Data Infrastructure. |
| **—** | **8100** | Arena | Spread Arena | Non-Existent | 0.0 | 0.00 | 0.0% | $0.00 | 0.0% | **BLOCKED** | Phantom port; code not in repository. |

---

## 2. Definitive Strategic Recommendations

1. **Top 3 Priority Modules for Immediate Paper V2 (`GO PAPER`)**:
   - **`v3.12 Cross-Basis Arbitrage` (8096)**: Highest PF (2.27), lowest drawdown (2.15%), delta-neutral market edge.
   - **`v3.8 NewDragon Trend Lab` (8091)**: Profitable 1h trend breakout with Stage 2 Maker profit lock (+$5.85 net, PF 1.28).
   - **`v3.1.4 Champion 4h` (8084)**: High-conviction 4h macro trend breakout (+$2.60 net, lowest fee burn).
2. **Conditional Systems (`CONDITIONAL`)**:
   - Kept in testing sandbox pending individual minimal patches (e.g. `v3.2` closed-bar fix, `v3.13` time-stop extension).
3. **Decommissioned & Blocked Systems (`BLOCKED` & `ARCHIVE`)**:
   - `v3.7` and `v3.9` blocked immediately to eliminate fee bleed.
   - `v3.5 Alpha Pairs` moved to Research Archive.
