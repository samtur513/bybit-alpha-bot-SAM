# FEE-CONTRACT-009B: Unified Fee & Execution Cost Standard

**Project:** Sovereign Quantum Fleet / TORG_BOT  
**Repository:** `samtur513/bybit-alpha-bot-SAM`  
**Standard Version:** v1.0 (FLEET-REPAIR-009B)  
**Implementation:** `src/fee_contract_009b.py`  
**Unit Tests:** `tests/test_fee_contract_009b.py` (9 passed, 100% pass)  
**Compliance Scope:** Backtest, Replay, Shadow Paper, TCA, and Ledger  

---

## 1. Executive Summary & Policy Mandate

In prior revisions (e.g. `generate_economic_readiness_009.py`), fee schedules assumed an unverified MNT token fee discount (Taker $0.090\%$, Maker $0.0324\%$). However, Bybit exchange policy explicitly excludes API-routed orders from standard UI MNT fee discounts unless an institutional VIP fee agreement is active on the account.

**Under FEE-CONTRACT-009B, the following operational invariants are enforced across the entire quantitative fleet:**
1. **Primary Fee Source**: The authoritative source of fee rates is the Bybit V5 account query `GET /v5/account/fee-rate` (filtered by `category=linear` and instrument `symbol`).
2. **Conservative Operator Fallback Profile**: If live account tariff data is unavailable (e.g. during offline research, deterministic replay, or uncredentialed shadow mode), the fleet **strictly enforces** the Conservative Operator Profile:
   - **Limit Maker Rate**: **$0.0360\%$** ($3.6$ bps, `Decimal("0.000360")`)
   - **Market Taker Rate**: **$0.1000\%$** ($10.0$ bps, `Decimal("0.001000")`)
3. **MNT Discount Prohibited in Production**: MNT discount rates ($0.0324\% / 0.0900\%$) are **strictly prohibited** as the baseline calculation for API trading, paper trading, and economic readiness gates. They are permitted **only** as a dedicated, explicitly labeled hypothetical sensitivity scenario (`is_sensitivity_scenario = True`).
4. **Leg-Level Execution Decomposition**: Every fill (whether full, partial, entry, or exit) must compute costs per executed leg, factoring in executed price, executed quantity, maker/taker classification, slippage, half-spread drag, and periodic funding cashflows.

---

## 2. Mathematical Specification

### A. Execution Fee Drag ($F_{\text{leg}}$)
For each executed leg with fill price $P_{\text{fill}}$, quantity $Q$, and fee rate $r_{\text{fee}}$:

$$\text{Gross Notional} = P_{\text{order}} \times Q$$

$$\text{Effective Notional} = P_{\text{fill}} \times Q$$

$$F_{\text{leg}} = \text{Effective Notional} \times r_{\text{fee}}$$

Where $r_{\text{fee}}$ is determined strictly by order execution type:
- **Limit Maker (Post-Only)**: $r_{\text{fee}} = r_{\text{maker}} = 0.000360$ ($0.036\%$)
- **Market Taker (Immediate/IOC)**: $r_{\text{fee}} = r_{\text{taker}} = 0.001000$ ($0.100\%$)

### B. Slippage Cost ($\text{Cost}_{\text{slip}}$)
Slippage reflects adverse price movement between signal issuance ($P_{\text{order}}$) and fill ($P_{\text{fill}}$):

$$P_{\text{fill}} = P_{\text{order}} \times \left(1 \pm \frac{\text{slip}_{\text{bps}}}{10000}\right)$$

$$\text{Cost}_{\text{slip}} = |P_{\text{fill}} - P_{\text{order}}| \times Q$$

- Maker orders: $\text{slip}_{\text{bps}} = 0$ (maker limit orders execute at limit price or better).
- Taker orders: $\text{slip}_{\text{bps}} \ge 2.0$ bps ($0.020\%$) in liquid markets; $\ge 5.0$ bps in volatile regimes.

### C. Spread Crossing Cost ($\text{Cost}_{\text{spread}}$)
Crossing the bid-ask spread incurs half-spread drag on taker orders:

$$\text{Cost}_{\text{spread}} = \text{Gross Notional} \times \frac{\text{half\_spread}_{\text{bps}}}{10000}$$

### D. Total Execution Drag ($D_{\text{exec}}$)
The total transaction cost incurred on an executed leg is:

$$D_{\text{exec}} = F_{\text{leg}} + \text{Cost}_{\text{slip}} + \text{Cost}_{\text{spread}}$$

### E. Funding Payment / Receipt ($C_{\text{funding}}$)
Perpetual positions held across 8-hour funding settlement epochs ($00:00$, $08:00$, $16:00$ UTC) incur funding cashflow:

$$C_{\text{funding}} = \begin{cases} - \text{Notional} \times FR, & \text{if Long} \\ + \text{Notional} \times FR, & \text{if Short} \end{cases}$$

Where $FR$ is the 8-hour funding rate (e.g. $+0.000300 = +3.0$ bps). Longs pay positive funding; Shorts receive positive funding.

---

## 3. Bybit V5 API Fee Rate Ingestion

When live API access is present, the module calls `GET /v5/account/fee-rate`:

```json
{
  "retCode": 0,
  "retMsg": "OK",
  "result": {
    "list": [
      {
        "symbol": "BTCUSDT",
        "makerFeeRate": "0.000200",
        "takerFeeRate": "0.000600"
      }
    ]
  }
}
```

- If `retCode == 0`, parsed rates are assigned provenance `BYBIT_API_V5`.
- If credentials are absent, network fails, or response is non-zero, fallback to `CONSERVATIVE_OPERATOR_DEFAULT` occurs automatically with audit warning.

---

## 4. Sensitivity Analysis vs Production Baseline

| Dimension | Production Baseline (FEE-CONTRACT-009B) | MNT Sensitivity Scenario (Hypothetical) | Variance Drag |
| :--- | :---: | :---: | :---: |
| **Maker Fee Rate** | **$0.0360\%$** ($3.6$ bps) | **$0.0324\%$** ($3.24$ bps) | $+0.36$ bps |
| **Taker Fee Rate** | **$0.1000\%$** ($10.0$ bps) | **$0.0900\%$** ($9.00$ bps) | $+1.00$ bps |
| **Roundtrip Maker-Maker** | $0.0720\%$ ($7.2$ bps) | $0.0648\%$ ($6.48$ bps) | $+0.72$ bps |
| **Roundtrip Maker-Taker** | $0.1360\%$ ($13.6$ bps) | $0.1224\%$ ($12.24$ bps) | $+1.36$ bps |
| **Roundtrip Taker-Taker** | $0.2000\%$ ($20.0$ bps) | $0.1800\%$ ($18.00$ bps) | $+2.00$ bps |

All strategies undergoing authorization in `ECONOMIC-GATE-010` must demonstrate positive net expectancy under the **$0.0360\% / 0.1000\%$** baseline.
