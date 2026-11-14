# Architectural & Code Differences: Original v3.2 vs Later Iterations
## Audit Deliverable 15 — V32-LEGACY-FORENSIC-001

---

## 1. Comparison Matrix: Original Port 8083 vs Newer Adapters

| Dimension | Original v3.2 on VPS (Port 8083) | Later Iterations / Adapters (`src/adaptive_fleet_adapter.py`) | Forensic Finding |
| :--- | :--- | :--- | :--- |
| **Strategy Scope** | **True Dual-Regime**: 1h Donchian Trend + Range Mean-Reversion. | Often reduced to pure **Range Mean-Reversion only** (`IRASRangeStrategy`). | Major divergence. Later adapters completely omitted the Trend Engine that generated 100% of the live profit! |
| **Trend Execution** | `CoreV3Engine` with Donchian Breakout, Half-Kelly Gear 3, and Cascade TP. | Omitted in some tests; replaced by surrogate simulator in ECONOMIC-GATE-010. | Explains why later tests showed different returns. |
| **Range Execution** | Paralyzed by `open=high=low=close` bug in `paper_trader_v32.py`. | Tested with discrete limit order crossing logic. | The original bot never actually traded Range in live paper testing. |
| **Universe Selection** | **Dynamic Screener across 400+ coins** (filtered by turnover, KER, ADX). | Often evaluated on a **static 18-coin list**. | The dynamic universe captured massive trending moves in altcoins (`AKE`, `HYPE`, `BTW`). |
| **Fee Assumptions** | Standard VIP0 Taker (0.100%) and 0.02% slippage. | Evaluated with MNT discounts (0.090% taker / 0.0324% maker) or standard fees. | Original code on VPS charged full VIP0 taker fees. |

---

## 2. Key Takeaway

The original v3.2 bot on port 8083 succeeded **not because of the Range Mean-Reversion engine, but because of its 1h Donchian Trend Engine scanning 400+ dynamic Bybit altcoins in a macro bear market**.
