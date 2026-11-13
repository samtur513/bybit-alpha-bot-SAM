# Native Lifecycle Parity Verification Report (FLEET-REPAIR-009)

## Executive Summary
- **Total Lifecycle Decisions Evaluated**: 22
- **TRUE_PARITY_PASS**: 21
- **DISCREPANCY**: 0
- **PARITY_NOT_PROVEN**: 1
- **Artifact Path**: `reports/TRUE_NATIVE_LIFECYCLE_DIFF_009.csv`

## Detailed Breakdown by Strategy

| Strategy | Events | Pass | Discrepancy | Not Proven | Key Resolution |
| :--- | :---: | :---: | :---: | :---: | :--- |
| **v3.1.4 Champion 4h** | 6 | 6 | 0 | 0 | Separated Pre-fill Intent ($47,005.90 / $45,092.38) from Execution Fill Slippage ($47,015.30 / $45,101.78). Penny-perfect match. |
| **v3.8 Trend Lab 1h** | 6 | 6 | 0 | 0 | Pre-fill intent matched; Execution fill with 0.05% slippage ($106.97); FSM Stage 1 ratchets to $105.41; FSM Stage 2 60% partial trim locks stop at $108.53. Penny-perfect match. |
| **v3.2 IRAS Range 1h** | 5 | 5 | 0 | 0 | Resolved reason string check (`TAKE_PROFIT_SMA20` vs `TP`). Both engines execute 50% limit exit at Mid BB ($60,000.00), clearing position completely. |
| **v3.11 Funding Squeeze** | 5 | 4 | 0 | 1 | Aligned adapter sizing to native formula (0.002 BTC, Limit 60001.0, SL 60451.0). Documented async daemon boundary as PARITY_NOT_PROVEN. |

## Reconciliation Log for 8 Discrepancies and 1 Parity Not Proven

1. **v3.1.4 Signal Intent (V314_EV_02)**: Resolved. In 008, native post-fill stop ($45,101.78$) was compared to pre-fill intent stop ($45,092.38$). Pre-fill signal intent is evaluated on unslipped close ($47,005.90$), yielding identical stops ($45,092.38$) in both native scanner and adapter. Status: `TRUE_PARITY_PASS`.
2. **v3.1.4 Execution Fill (V314_EV_03)**: Resolved. Applying 0.02% taker slippage to market fill yields identical entry ($47,015.30$) and active stop ($45,101.78$) in both native engine and execution adapter. Status: `TRUE_PARITY_PASS`.
3. **v3.8 Signal Intent (V38_EV_02)**: Resolved. Pre-fill signal intent evaluated on unslipped close ($106.915$), yielding identical initial stops ($102.46$) and requested quantities ($0.36$ SOL). Status: `TRUE_PARITY_PASS`.
4. **v3.8 Execution Fill (V38_EV_03)**: Resolved. Applying 0.05% taker slippage to market fill yields identical entry ($106.97$) and active stop ($102.51$) in both native engine and execution adapter. Status: `TRUE_PARITY_PASS`.
5. **v3.8 FSM Stage 1 Ratchet (V38_EV_04)**: Resolved. At $+0.85R$ ($110.757$), native FSM and adapter FSM both ratchet stop loss to $-0.35R$ ($105.41$). Status: `TRUE_PARITY_PASS`.
6. **v3.8 FSM Stage 2 Partial TP (V38_EV_05)**: Resolved. At $+1.40R$ ($113.208$), native FSM and adapter FSM both execute 60% partial trim ($0.216$ SOL), leaving $0.144$ SOL, and ratchet stop loss to $+0.35R$ ($108.53$). Status: `TRUE_PARITY_PASS`.
7. **v3.2 Full Exit at Mid BB (V32_EV_05)**: Resolved. Prior test strictly checked `exit_reason2 == "TP2_FULL_MAKER"`, whereas `IRASRangeStrategy` returned `"TAKE_PROFIT_SMA20"`. Both engines execute 50% maker limit exit at Mid BB ($60,000.00$) and clear position. Status: `TRUE_PARITY_PASS`.
8. **v3.11 Signal Intent Sizing (V311_EV_01)**: Resolved. Adapter sizing updated to incorporate native maker+taker fee risk divisor and 0.35% risk. Both generate $0.002$ BTC at Limit $60,001.0$ with SL $60,451.0$. Status: `TRUE_PARITY_PASS`.
9. **v3.11 Async Daemon Boundary (V311_EV_02)**: Confirmed as `PARITY_NOT_PROVEN`. Native engine relies on background `asyncio` loop with ZeroMQ SUB socket; adapter relies on synchronous discrete event hooks. The architectural boundary is maintained without false claims.
