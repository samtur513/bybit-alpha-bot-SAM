# Execution Realism & Altcoin Market Order Audit
## Audit Deliverable 09 — V32-LEGACY-FORENSIC-001

---

## 1. Market Order Realism Evaluation

All 28 executed trades in v3.2 Dual-Regime were **Taker Market Orders** emitted by the 1h Donchian Breakout trend engine.
- **Execution Certainty:** Taker market orders on Bybit Linear Perpetuals have a 100% fill probability. Zero orders suffered from queue position loss or missed limit fills.
- **Taker Fee Deduction:** All trades properly paid the standard Bybit VIP0 taker fee of $0.100\%$ on entry and exit.
- **Slippage Modeling:** The original code embedded a fixed $0.020\%$ slippage.
  - On liquid pairs (`BTCUSDT`, `ETHUSDT`, `SOLUSDT`), $0.02\%$ is realistic.
  - On dynamic screener altcoins (`AKEUSDT`, `XPLUSDT`, `CASHCATUSDT`), realistic market impact during a momentum breakout typically ranges from $0.05\%$ to $0.10\%$.

---

## 2. Robustness Under Elevated Slippage

Even when tripling slippage to $0.05\%$ (Scenario C) or quintupling to $0.10\%$ (Scenario D), the system remains solidly profitable:
- Scenario C Net PnL: **+$16.71** (Return: **+5.57%**)
- Scenario D Net PnL (Excluding top win `AKEUSDT`): **+$11.08** (Return: **+3.69%**, Profit Factor: **1.72**)
