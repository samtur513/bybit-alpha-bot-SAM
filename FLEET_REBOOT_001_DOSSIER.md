# FLEET-REBOOT-001: Master Sovereign Quantum Fleet Audit & Paper V2 Readiness Dossier

**Project**: Sovereign Quantum Fleet / Bybit Multi-Strategy Trading Fleet  
**Repository**: `samtur513/bybit-alpha-bot-SAM`  
**Git Branch**: `test/f1a-regression-suite`  
**Commit SHA**: `412c54fb72fe1b8ea2717992cb5bb7bf9b93b827`  
**Audit Protocol**: FLEET-REBOOT-001 (Steps 1, 2, 3 Complete)  
**Evaluator**: Lead Quant Engineer & Independent Financial Auditor  

---

## 1. Executive Summary & Fleet Triage Overview

```
Total Systems Audited:          18 Active Services (+ 2 Baselines + Meta-Dashboard)
-----------------------------------------------------------------------------------
[ GO PAPER ]                    3 (v3.12 Cross-Basis, v3.8 Trend Lab, v3.1.4 Champion)
[ CONDITIONAL ]                10 (v3.2, v3.11, v3-v4, v3.3, v3.4, v3.6, v3.14, v3.13, 8098, 8093)
[ BLOCKED ]                     3 (v3.7 Master Sweeps, v3.9 Scalp Lab, 8100 Spread Arena)
[ RESEARCH ARCHIVE ]            1 (v3.5 Alpha Pairs - audited in F2.2)
[ OPERATIONAL INFRA ]           2 (Port 8085 Meta-Dashboard, Port 8095 Data Hub)
```

---

## 2. Priority Strategy Scoreboard (Paper V2 Shadow Pilot)

| Rank | Port | Module ID | Strategy Name | Trades ($N$) | Win Rate (%) | Gross PnL ($) | Fees ($) | Net PnL ($) | Profit Factor | Max DD (%) | Final Decision |
| :---: | :---: | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
| **1** | **8096** | **v3.12** | **Cross-Basis Arbitrage** | **12** | **83.33%** | +$14.80 | $6.50 | **+$8.30** | **2.27** | **2.15%** | **GO PAPER** |
| **2** | **8091** | **v3.8** | **NewDragon Trend Lab** | **48** | **43.75%** | +$18.25 | $12.40 | **+$5.85** | **1.28** | **11.20%** | **GO PAPER** |
| **3** | **8084** | **v3.1.4**| **Champion 4h** | **22** | **45.45%** | +$12.40 | $9.80 | **+$2.60** | **1.15** | **8.42%** | **GO PAPER** |
| 4 | 8083 | v3.2 | Dual-Regime IRAS | 38 | 42.10% | +$10.30 | $14.50 | -$4.20 | 0.82 | 16.40% | **CONDITIONAL** |
| 5 | 8097 | v3.11 | Funding Squeeze | 14 | 57.10% | +$6.10 | $8.20 | -$2.10 | 0.91 | 14.20% | **CONDITIONAL** |
| 6 | 8094 | v3.14 | Basket Dispersion | 22 | 27.30% | +$7.20 | $15.60 | -$8.40 | 0.62 | 19.80% | **CONDITIONAL** |
| 7 | 8080 | v3.13 | Sector Arbitrage | 54 | 18.50% | +$5.60 | $22.40 | -$16.80 | 0.45 | 24.50% | **CONDITIONAL** |
| 8 | 8088 | v3.5 | Alpha Pairs | 68 | 26.50% | +$2.70 | $31.20 | -$28.50 | 0.58 | 28.40% | **RESEARCH ARCHIVE** |
| 9 | 8090 | v3.7 | Master Sweeps | 45 | 0.00% | -$14.20 | $18.20 | -$32.40 | 0.00 | 32.40% | **BLOCKED** |
| 10 | 8092 | v3.9 | NewDragon Scalp Lab | 62 | 0.00% | -$16.40 | $24.80 | -$41.20 | 0.00 | 41.20% | **BLOCKED** |

---

## 3. Master Deliverables Directory

All 15 deliverables generated under FLEET-REBOOT-001 are tracked, committed, and independently auditable:

### Step 1: Fleet Discovery & Economic Triage
- [`FLEET_DISCOVERY_MATRIX.md`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/FLEET_DISCOVERY_MATRIX.md): Complete 18-engine technical inventory.
- [`FLEET_P0_DEFECTS.md`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/FLEET_P0_DEFECTS.md): Forensic catalog of data, execution, accounting, and risk defects.
- [`FLEET_ECONOMIC_TRIAGE.md`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/FLEET_ECONOMIC_TRIAGE.md): 5-dimension quantitative scoring and ranking.
- [`FLEET_DEPLOYMENT_MAP.md`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/FLEET_DEPLOYMENT_MAP.md): Systemd unit map, ZMQ sockets, and process topologies.

### Step 2: Minimal Repair & Validation
- [`PAPER_EXECUTION_CONTRACT.md`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/PAPER_EXECUTION_CONTRACT.md): Universal event pipeline and data schemas.
- [`FINANCIAL_INVARIANTS_REPORT.md`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/FINANCIAL_INVARIANTS_REPORT.md): 20/20 regression tests verification report.
- [`SIGNAL_PARITY_FLEET.csv`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/SIGNAL_PARITY_FLEET.csv): Invariance proof for alpha indicators across all versions.
- [`FLEET_REPAIR_RESULTS.md`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/FLEET_REPAIR_RESULTS.md): Architectural report on fee, slice, and ledger repairs.
- [`LOCAL_REPLAY_RESULTS.md`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/LOCAL_REPLAY_RESULTS.md): 18-month replay pilot on historical parquet data.
- [`PAPER_READINESS_MATRIX.md`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/PAPER_READINESS_MATRIX.md): Readiness categorization by module.

### Step 3: Verified Shadow Paper & Staging
- [`PAPER_V2_PILOT_REPORT.md`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/PAPER_V2_PILOT_REPORT.md): 90-day forward shadow pilot performance analysis.
- [`FLEET_VERIFIED_SCOREBOARD.csv`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/FLEET_VERIFIED_SCOREBOARD.csv): Master scoreboard table.
- [`FLEET_ECONOMIC_RANKING.md`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/FLEET_ECONOMIC_RANKING.md): Fleet-wide economic ranking table.
- [`PAPER_V2_DEPLOYMENT_PLAN.md`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/PAPER_V2_DEPLOYMENT_PLAN.md): Safe, isolated VPS staging procedure (requires human sign-off).
- [`GO_NO_GO_DECISION.md`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/GO_NO_GO_DECISION.md): Final gate verdicts by engine.
- [`reports/FLEET_REBOOT_001_BUNDLE.zip`](file:///c:/projects/Torg_Bot_3%20-Antigraviti/reports/FLEET_REBOOT_001_BUNDLE.zip): 56 KB complete offline package.
