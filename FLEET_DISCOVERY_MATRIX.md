# FLEET_DISCOVERY_MATRIX.md: Sovereign Quantum Fleet System Inventory

**Audit Date**: November 12, 2026  
**Audit Protocol**: FLEET-REBOOT-001 (Step 1 Fleet Discovery)  
**Evaluator**: Lead Quant Engineer & Independent Financial Auditor  
**Scope**: 18 Active Fleet Services + 2 Historical Baselines (v2.1, v3.0) + Meta-Dashboard  

---

## 1. Complete Fleet Inventory Matrix

| Port | Strategy / Role | Directory | Core Engine File | Data & Price Source | Timeframe / Frequency | Multi-Leg Structure | Fee Model Configured | Funding Accounting | Ledger Architecture | State Recovery | Snapshot Equity | Closed Trades | Win Rate (Snap) | Active Pos | Historical Data Status | P0 Defects Summary | Paper Readiness |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **8080** | v3.13 Sector Arbitrage | `v3_13_sector_arb` | `src/sector_arb_8080.py` | Bybit V5 Public WS (Linear) | Real-time / 15m z-score | 2 Legs (Long Alt A / Short Alt B) | 0.090% T / 0.0324% M (MNT Discount) | Logged via TCA | `ShadowTCALedger` (SQLite WAL) | JSON State + SQLite | $294.00 | 33 | 6.1% | 0 | 18 coins 1h/4h parquet available | MNT fee bias; unhedged leg timeout risk; time-stop churn | **CONDITIONAL** |
| **8082** | v3-v4 NewDragon Trend Lab | `v3_v4_exp` | `src/paper_trader_v3_v4.py` | Bybit V5 WS + REST klines | 1h Donchian + SRIE Alpha | Single-leg directional | 0.090% Taker (MNT) | None (Omitted) | In-memory + JSON file | Atomic JSON write | $299.86 | 4 | 50.0% | 0 | 18 coins available (2024–2026) | Funding omitted; fee drag under-reported; small sample | **CONDITIONAL** |
| **8083** | v3.2 Dual-Regime IRAS | `v3_2` | `src/paper_trader_v32.py` | Bybit V5 WS + REST klines | 1h Trend (Donchian) + Range (Bollinger) | Single-leg directional | 0.090% T / 0.0324% M (MNT) | Approximate (1h mark) | In-memory + JSON file | Atomic JSON write | $311.00 | 19 | 57.9% | 1 | 18 coins available (2024–2026) | Partial TP unlinked in closed_trades; MNT fee bias; synthetic quotes | **CONDITIONAL** |
| **8084** | v3.1.4 Champion 4h | `v3_1_4` | `src/paper_trader_v314.py` | Bybit V5 WS (18 coins) | 4h Donchian Breakout | Single-leg directional | 0.090% Taker (MNT) | Approximate 8h settlement | In-memory + JSON file | Atomic JSON write | $298.04 | 2 | 0.0% | 4 | 18 coins available (2024–2026) | Pyramiding entry price distortion; MNT fee bias; low trade sample | **GO PAPER (Priority 1)** |
| **8085** | Meta-Dashboard | `meta_dashboard` | `meta_server.py` | HTTP polling of ports 8080–8098 | 5s background poll | Telemetry Aggregator | N/A (Non-trading) | N/A | In-memory cache | Stateless poll | $4,502.44 | 0 | N/A | 0 | N/A | Stale cache display; potential memory leak on long runs | **OPERATIONAL** |
| **8086** | v3.3 Turbo Streak Risk | `v3_3` | `src/paper_trader_v33.py` | Bybit V5 WS klines | 1h Breakout + Streak Sizing | Single-leg directional | 0.090% Taker (MNT) | None (Omitted) | In-memory + JSON file | Atomic JSON write | $300.30 | 3 | 33.3% | 0 | 18 coins available (2024–2026) | Machine-gun streak penalty; funding omitted; micro-sample ($N=3$) | **CONDITIONAL** |
| **8087** | v3.4 ZF-8 Quant | `v3_4` | `src/paper_trader_v34.py` | Bybit V5 WS klines | 1h Breakout + ZF-8 Gears | Single-leg directional | 0.090% Taker (MNT) | None (Omitted) | In-memory + JSON file | Atomic JSON write | $299.74 | 13 | 23.1% | 1 | 18 coins available (2024–2026) | Gear shift lag; funding omitted; high churn in chop | **CONDITIONAL** |
| **8088** | v3.5 Alpha Pairs | `v3_5` | `src/paper_trader_v35.py` | Bybit V5 WS klines | 4h Rebalance / 72h Momentum | 2 Legs (Long Top 2 / Short Bot 2) | 0.100% Taker (Fixed in F1C) | Deducted via REST mark | `FinancialLedger` (v3.5 repaired) | SQLite WAL + Idempotency | $320.92 | 4 | 75.0% | 3 | 18 coins available (2024–2026) | **F2.2 AUDITED**: Negative net alpha (-$102 18M); paper WR 75% was $N=4$ artifact | **RESEARCH ARCHIVE** |
| **8089** | v3.6 Omega ZF-8 | `v3_6` | `src/paper_trader_v36.py` | Bybit V5 WS klines | 1h Dual-Regime + ZF-8 | Single-leg directional | 0.090% T / 0.0324% M (MNT) | Approximate 8h settlement | In-memory + JSON file | Atomic JSON write | $297.51 | 12 | 16.7% | 0 | 18 coins available (2024–2026) | High parameter complexity; regime whipsaw; MNT fee bias | **CONDITIONAL** |
| **8090** | v3.7 Master Sweeps | `v3_7` | `src/scalp_engines.py` | Bybit V5 WS L2 Orderbook | 1m/5m Orderbook Sweeps | Single-leg scalper | 0.090% T / 0.0324% M (MNT) | None (Intraday) | In-memory + JSON file | Atomic JSON write | $293.45 | 19 | 0.0% | 0 | High-frequency tick data required (limited) | 0.0% WR in paper; adverse selection on limit fills; toxic sweeps | **BLOCKED** |
| **8091** | v3.8 NewDragon Trend Lab | `v3_8` | `src/paper_trader_v38.py` | Bybit V5 WS klines | 1h Donchian + 3-Stage Trail | Single-leg directional | 0.090% T / 0.0324% M (MNT) | None (Omitted) | In-memory + JSON file | Atomic JSON write | $305.47 | 9 | 55.6% | 2 | 18 coins available (2024–2026) | Stage 2 maker fill assumption unverified; funding omitted | **GO PAPER (Priority 2)** |
| **8092** | v3.9 NewDragon Scalp Lab | `v3_9` | `src/microstructure_v39.py` | Bybit V5 WS Orderbook L2 | Sub-minute Orderbook Flow | Single-leg scalper | 0.090% T / 0.0324% M (MNT) | None (Intraday) | In-memory + JSON file | Atomic JSON write | $299.78 | 39 | 0.0% | 0 | High-frequency tick data required (limited) | 0.0% WR ($N=39$); time-stop bleed; maker queue position unmodeled | **BLOCKED** |
| **8093** | v3.7-v4 NewDragon Scalp Lab | `v3_7_v4_exp` | `src/scalp_engines_v4.py` | Bybit V5 WS L2 Orderbook | 1m/5m Lead-Lag Microstructure | Single-leg scalper | 0.090% T / 0.0324% M (MNT) | None (Intraday) | In-memory + JSON file | Atomic JSON write | $299.69 | 43 | 39.5% | 0 | High-frequency tick data required (limited) | High fee drag; BTC reversal stop churn; maker fill uncertainty | **CONDITIONAL** |
| **8094** | v3.14 Basket Dispersion | `v3_14_basket_dispersion` | `src/dispersion_8094.py` | Bybit V5 WS (Alt + BTC + ETH) | Multi-leg spread z-score | 3 Legs (Long Alt / Short BTC + ETH) | 0.090% T / 0.0324% M (MNT) | Realized on Alt/BTC/ETH | `ShadowTCALedger` (SQLite WAL) | JSON State + SQLite | $299.34 | 8 | 25.0% | 0 | 18 coins available (2024–2026) | Tri-leg execution synchronization; roundtrip basket fee bleed | **CONDITIONAL** |
| **8095** | Data & TCA Hub | `v3_10_sweep` | `hub_8095.py` | ZeroMQ PUB/SUB + WS | 100ms Tick Aggregator | Market Data Infrastructure | N/A (Data Hub) | N/A | `ShadowTCALedger` (SQLite WAL) | SQLite WAL | $300.00 | 0 | N/A | 0 | Live streaming only | Socket binding timing on Windows; missing tick recovery | **OPERATIONAL** |
| **8096** | v3.12 Cross-Basis Arbitrage | `v3_12_crossbasis` | `crossbasis_8096.py` | Spot WS + Perp WS | 1s basis spread deviation | 2 Legs (Long Spot / Short Perp) | 0.090% T / 0.0324% M (MNT) | Accurate Perp funding collection | `ShadowTCALedger` (SQLite WAL) | JSON State + SQLite | $296.78 | 0 | N/A | 0 | Spot + Perp L1 data required | Spot vs Perp fee asymmetry; transfer friction unmodeled | **GO PAPER (Priority 3)** |
| **8097** | v3.11 Funding Squeeze | `v3_11_funding` | `funding_8097.py` | Bybit 8h Funding Rate API | 8h Settlement Harvesting | Directional Basis Squeeze | 0.090% T / 0.0324% M (MNT) | Explicit 8h funding payment | `ShadowTCALedger` (SQLite WAL) | JSON State + SQLite | $286.21 | 0 | N/A | 0 | Funding rate historical tables available | Squeeze price adverse excursion > funding yield; 4.6% DD without trades | **CONDITIONAL** |
| **8098** | Master Sweeps L2 | `v3_10_sweep` | `sweep_8098.py` | ZMQ from Hub 8095 | Sub-second L2 Orderbook | Single-leg sweep reclaims | 0.090% T / 0.0324% M (MNT) | None (Intraday) | `ShadowTCALedger` (SQLite WAL) | SQLite WAL | $300.00 | 0 | N/A | 0 | High-frequency L2 orderbook required | Hub dependency; unverified maker fill rate in fast markets | **CONDITIONAL** |
| **Baseline** | v2.1 4h Breakout | `v2_1` | `src/paper_trader_v2.py` | Bybit REST/WS | 4h Donchian Breakout | Single-leg directional | 0.090% Taker (MNT) | None | JSON file | Atomic JSON write | $300.00 | N/A | N/A | 0 | 18 coins available (2024–2026) | Static 18-coin universe; no dynamic screener; MNT fee bias | **HISTORICAL BASELINE** |
| **Baseline** | v3.0 Production Freeze | `v3` | `src/paper_trader_v3.py` | Bybit V5 Dynamic Screener | 4h Breakout + News Core Shield | Single-leg directional | 0.090% Taker (MNT) | None | JSON file | Atomic JSON write | $300.00 | N/A | N/A | 0 | 400+ coins screening data | MNT fee bias; requires 400+ coin WS connections | **HISTORICAL BASELINE** |
| **8100** | Spread Arena | None | None | None | None | None | None | None | None | None | N/A | N/A | N/A | N/A | None | **NOT FOUND IN CODEBASE OR SERVICES** | **BLOCKED** |

---

## 2. Key Architecture Groupings

The 18 systems naturally segregate into four quantitative archetypes:

1. **Macro Trend & Breakout Engines (HTF: 1h / 4h)**:
   - `v3.1.4` (8084), `v3.8` (8091), `v3-v4` (8082), `v3.3` (8086), `v3.4` (8087), `v3.2` (8083), `v3.6` (8089).
   - *Characteristics*: 1h or 4h closed candles, Donchian channels, ADX/EMA trend filters, trailing stops.
   - *Strengths*: Highly testable with zero lookahead on available parquet data (2024–2026).
   - *Vulnerabilities*: Chop decay in sideways regimes, taker fee burn on frequent false breakouts.
2. **Multi-Leg Relative Value & Arbitrage (LTF/MTF: 15m / 1h)**:
   - `v3.13` Sector Arb (8080), `v3.14` Basket Dispersion (8094), `v3.5` Alpha Pairs (8088), `v3.12` Cross-Basis (8096).
   - *Characteristics*: Beta-neutral or market-neutral pairs/baskets trading statistical spread or basis dislocations.
   - *Strengths*: Insulated from broad market direction ($Beta \approx 0$).
   - *Vulnerabilities*: Multi-leg execution friction, broken leg unhedged directional exposure, double/triple fee drag.
3. **Carry & Microstructure Harvesting (Event-Driven: 8h / Continuous)**:
   - `v3.11` Funding Squeeze (8097), `v3.10` Data Hub (8095), `v3.12` Spot-Perp (8096).
   - *Characteristics*: Harvesting structural funding cashflows or orderbook imbalances.
   - *Strengths*: Positive funding yield.
   - *Vulnerabilities*: Adverse price moves exceeding funding yield (funding squeeze risk).
4. **High-Frequency & Scalping Lab (Sub-Minute / L2 Orderbook)**:
   - `v3.7` (8090), `v3.9` (8092), `v3.7-v4` (8093), `v3.10` Sweeps (8098).
   - *Characteristics*: Microstructure imbalance, orderbook sweeps, CVD flips.
   - *Strengths*: Rapid turnover, short holding time (< 1 minute).
   - *Vulnerabilities*: Severe adverse selection on passive maker limits; in paper, reported 0.0% Win Rate ($N=19$ and $N=39$) due to taker fees on stops.
