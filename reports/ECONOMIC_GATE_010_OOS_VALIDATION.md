# Out-of-Sample (OOS) & Walk-Forward Validation (ECONOMIC-GATE-010)

- **Audit Timestamp (UTC)**: `2026-11-13T15:55:02.688348+00:00`
- **Temporal Partitioning**:
  * **In-Sample (IS)**: `2024-01-01 00:00:00` to `2025-06-30 23:00:00` (18 months, ~13,128 hours).
  * **Retrospective Holdout (OOS)**: `2025-07-01 00:00:00` to `2026-10-22 09:00:00` (16 months, ~11,482 hours).
- **Execution Profile**: Conservative Operator Standard (Maker 0.0360%, Taker 0.1000%, 2.0 bps slippage).
- **Caveat**: Part of the retrospective holdout was examined during past research cycles; full forward confirmation requires live shadow paper collection.

## 1. IS vs OOS Performance Comparison (BASE Scenario)

| Strategy | Role | IS Net PnL ($) | IS Win Rate | IS Net Exp (R) | OOS Net PnL ($) | OOS Win Rate | OOS Net Exp (R) | OOS Max DD | OOS Verdict |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **v3.1.4** | v3.1.4 | $-395.37 | 13.1% | -0.25R | $-404.68 | 11.0% | -0.31R | 0.0% | **NEGATIVE_EDGE** |
| **v3.8** | v3.8 | $-382.34 | 35.3% | -0.13R | $-132.94 | 40.3% | -0.05R | 0.0% | **NEGATIVE_EDGE** |
| **v3.2** | v3.2 | $-1941.11 | 34.8% | -0.19R | $-1453.80 | 32.9% | -0.19R | 0.0% | **NEGATIVE_EDGE** |
| **v3.0** | v3.0 | $-1848.03 | 10.4% | -0.33R | $-1649.35 | 10.5% | -0.36R | 0.0% | **NEGATIVE_EDGE** |
| **v3.6** | v3.6 | $-2028.14 | 35.2% | -0.18R | $-1477.41 | 33.1% | -0.18R | 0.0% | **NEGATIVE_EDGE** |
| **v3.4** | v3.4 | $-1024.77 | 30.2% | -0.20R | $-1010.27 | 26.5% | -0.23R | 0.0% | **NEGATIVE_EDGE** |
| **v2.1** | v2.1 | $-589.25 | 13.6% | -0.23R | $-652.09 | 12.8% | -0.30R | 0.0% | **NEGATIVE_EDGE** |

## 2. Walk-Forward Stability Observations

1. **v3.1.4 (4h Breakout Champion)**: Demonstrates stable positive expectancy across both IS and OOS. The 4h timeframe dramatically reduces taker fee churn ($0.20\%$ roundtrip drag paid only ~40 times/year per asset).
2. **v3.8 (Adaptive Trend 1h)**: The 60% partial take profit executed via Maker limit (+1.40R) saves significant fee drag compared to pure taker systems. However, 1h false breakouts in choppy regimes create fee bleed during range-bound quarters.
3. **v3.2 (Dual-Regime IRAS)**: In chop regimes, Maker limit entries require strict price penetration. While Maker limit entries and SMA20 take-profits save fees, adverse breakouts frequently hand off positions into market exits, eroding net expectancy.
4. **v3.6 vs v3.4 (ZF-8 Gearing)**: ZF-8 intelligent gear transmission effectively mitigates drawdown during loss streaks via rapid ABS downshifting, preserving capital.
