# MAKER_UPPER_BOUND.md: Fee Sensitivity & Maker/Taker Execution Bounds

**Evaluation Scope**: Bybit VIP0 Fee Tier (Taker: 0.1000%, Maker: 0.0360%, No MNT discount)  
**Strategy**: Alpha Pairs v3.5 across 18 Months (13,128 Hourly Bars, 18 Instruments)  
**Diagnostic Purpose**: Sensitivity upper bound analysis (NOT a promise of fillability).

---

## 1. Fee Architecture Regimes

| Regime | Execution Assumption | Effective Round-Trip Fee | Estimated Total Fees Paid ($) | Fee Reduction vs E0 ($) | Feasibility Classification |
| :--- | :--- | :---: | :---: | :---: | :--- |
| **E0: Baseline Taker** | 100% Taker Market Orders | **0.2000%** (20.0 bps) | $172.90 | $0.00 (0.0%) | **FULLY EXECUTABLE** (Current Engine) |
| **E1: Pure Maker** | 100% Post-Only Limit Fills | **0.0720%** (7.2 bps) | $62.24 | -$110.66 (-64.0%) | **UNFILLABLE THEORETICAL UPPER BOUND** |
| **E2-A: Conservative Hybrid** | 30% Maker Fills, 70% Taker Fallback | **0.1616%** (16.2 bps) | $139.70 | -$33.20 (-19.2%) | Realistically Attainable |
| **E2-B: Balanced Hybrid** | 50% Maker Fills, 50% Taker Fallback | **0.1360%** (13.6 bps) | $117.57 | -$55.33 (-32.0%) | High Passive Execution Skill |
| **E2-C: Aggressive Hybrid** | 70% Maker Fills, 30% Taker Fallback | **0.1104%** (11.0 bps) | $95.44 | -$77.46 (-44.8%) | Optimistic Ceiling |

---

## 2. Structural Execution Frictions of Passive Quoting

While pure Maker execution (E1) theoretically saves up to **64%** of exchange fee burn, it introduces severe structural market microstructure risks:

1. **Adverse Selection**: Passive limit orders are filled primarily when toxic order flow runs through the book. When a strong momentum breakout occurs, the limit order is either front-run or left unfilled.
2. **Broken Pair Leg Risk**: If Leg A (Long) fills as a Maker limit, but Leg B (Short) remains pending and times out, the portfolio is exposed to **100% directional unhedged beta** against the market.
3. **Execution Delay Slippage**: Waiting for limit order fills delays trade entry by 1–4 hours, causing the relative strength edge to deteriorate.
4. **Opportunity Cost**: Unfilled limit orders result in missed signals that must be accounted for in the Missed Intent Ledger.

---

## 3. Net Economic Impact on Strategy Viability

Even under the theoretical **E1 Upper Bound** (-64% fee reduction):
- Because the strategy's average gross alpha per trade is only **+0.18%**, and the round-trip Maker fee is **0.072%**, a positive gross margin remains vulnerable to crossing spread on stops and funding drag.
- Therefore, passive maker execution is a **necessary component**, but alone is **insufficient** to make naive 72h momentum robustly profitable without market regime filtering.
