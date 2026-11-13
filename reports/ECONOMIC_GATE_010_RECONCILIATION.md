# Historical Negative Control Reconciliation (ECONOMIC-GATE-010)

- **Audit Timestamp (UTC)**: `2026-11-13T15:55:02.688348+00:00`
- **Purpose**: Fully reconcile and explain discrepancies between previous research claims and current conservative audit findings.

## 1. Reconciliation Table: Previous Negative Controls vs Gate 010

| System / Submodule | Previous Claimed Result | Previous Finding in Audit | Gate 010 Replay Finding | Mathematical Root Cause of Discrepancy |
| :--- | :--- | :--- | :--- | :--- |
| **v3.2 IRAS Range Maker** | Claimed Maker edge | `IRAS_RUN_REGISTRY.csv` showed -$14.80 in Q3 2026, -$4.26 in Q2 2026 | Negative Net Expectancy in Chop | Touch fill vs penetration requirement. Bandwidth target (+1.25%) is frequently truncated by stop-loss or regime handoff market exits before SMA20 target is reached. |
| **v3.1.4 (4h Trend Breakout)** | +9,867% PnL ($29.9k) | `HISTORICAL_AUDIT_2024_2026.md` | Realistic +20% to +60% uncompounded | Previous audit compounded equity dynamically on 2.5% Half-Kelly on a single pool. Under uncompounded $300 isolated capital and 1.0% risk, real dollar gain is consistent with +0.15R to +0.35R per trade. |
| **v3.8 (Adaptive 1h FSM)** | Claimed 9 bps taker fee | 10% MNT discount ($0.090\%$) invalid for API | $0.100\%$ Taker / $0.036\%$ Maker | Bybit does not apply MNT discount to API-routed orders. Applying real 10.0 bps taker fee increases roundtrip cost by 2.0 bps, reducing 1h net expectancy. |
| **v3.0 Dynamic Screener** | 400+ coin universe | Lacked historical parquet | 18-coin proxy universe tested | The 400-coin universe data does not exist in local parquets. Tested as an 18-coin proxy, with 400-coin screener flagged as `BLOCKED_DATA`. |

## 2. Key Forensic Takeaway
No synthetic alpha or compounding illusions: all 7 systems evaluated strictly with $300.00 isolated capital, conservative operator fees (0.036% maker / 0.100% taker), realistic order queue penetration, and empirical funding cashflow.
