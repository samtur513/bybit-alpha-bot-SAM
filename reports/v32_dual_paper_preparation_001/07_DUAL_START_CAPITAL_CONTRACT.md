# 07_DUAL_START_CAPITAL_CONTRACT.md
## Formal Invariant Specification & Dual-Capital Contract: Original vs Shadow v3.2

- **Target Systems:**
  - **System A:** v3.2 ORIGINAL Donchian Trend (`paper_trader_v32.py`)
  - **System B:** v3.2 LEGACY SHADOW Donchian Trend (`paper_trader_v32_shadow.py`)
- **Starting Capital Allocation:** Exactly **300.000000 USDT** for each system.
- **Starting Moment:** Synchronized $T_0$ (UTC timestamp to the exact second).

---

### 1. Dual Portfolio Starting Contract

| Contract Parameter | System A: v3.2 ORIGINAL | System B: v3.2 LEGACY SHADOW | Invariant Verification Rule |
| :--- | :---: | :---: | :--- |
| **Initial Capital ($C_0$)** | **300.000000 USDT** | **300.000000 USDT** | Strict mathematical equality: $C_0^A = C_0^B = 300.0$ |
| **Realized PnL at $T_0$** | **0.000000 USDT** | **0.000000 USDT** | Zero historical carryover |
| **Unrealized PnL at $T_0$** | **0.000000 USDT** | **0.000000 USDT** | Zero floating profit/loss |
| **Open Positions at $T_0$** | **0** | **0** | Clean sheet: $\text{len}(\text{positions}) = 0$ |
| **Pending Limit/Stop Orders** | **0** | **0** | Zero unfulfilled intents |
| **Trading Horizon Start** | **$T_0$ UTC** | **$T_0$ UTC** | Exact identical second of first candle evaluation |
| **Core Trading Logic** | Donchian Trend 120 bars, 1H | Donchian Trend 120 bars, 1H | 100% Identical mathematical alpha generation |
| **Timeframe & Lookahead** | 1H, `shift(1)` on closed bar | 1H, `shift(1)` on closed bar | PIT Causality strictly enforced |
| **Take Profit Rules** | TP1 @ 1.5R ($30\%$) / TP2 @ 3.0R ($40\%$) | TP1 @ 1.5R ($30\%$) / TP2 @ 3.0R ($40\%$) | Identical target levels |
| **Execution Model** | Native Legacy Paper | Realistic Shadow Paper | See Section 2 |
| **Fee Model** | 0.090% (Legacy MNT simulated) | **0.100% Taker / 0.036% Maker (VIP0)** | Realism audit |
| **Slippage Simulation** | Fixed 0.02% | Orderbook Top/Depth + Spread | Realism audit |

---

### 2. Execution Model Delineation

$$\text{Realized PnL}_{\text{trade}} = \text{Direction} \times (P_{\text{exit}} - P_{\text{entry}}) \times \text{Qty} - \text{Fees}_{\text{entry}} - \text{Fees}_{\text{exit}}$$

$$\text{Realized Equity}(T) = 300.000000 + \sum_{i=1}^{N} \text{Realized PnL}_i$$

1. **System A (Original Legacy Paper):**
   - Employs the historical execution loop (`kind == 'price'`).
   - Uses simulated slippage of $0.02\%$ applied to tick/bar prices.
   - Assumes $0.09\%$ flat taker fee.
2. **System B (Realistic Legacy Shadow):**
   - Evaluates trades against Bybit Level-2 Orderbook Best Bid/Ask (`best_bid`, `best_ask`).
   - Applies Bybit VIP0 standard fees: **$0.100\%$ Taker** / **$0.036\%$ Maker**.
   - Validates bar volume and minimum notional ($\ge 5.0$ USDT).
   - Records any price deviation or rejected execution to the `MissedIntentLedger`.
   - Logs microsecond timestamps for every partial fill and stop ratchet to SQLite.

---

### 3. Synchronized Deployment Options

To satisfy the Critical Rule of **Часть IV** ("Если оригинальный процесс невозможно безопасно перевести на новый Paper-период без потери данных, подготовить отдельный идентичный экземпляр оригинального кода"):

- **Deployment Pattern 1 (Recommended — Zero Historical Loss):**
  - Historical v3.2 on Port 8083 remains `KEEP_RUNNING` (preserving its 28 trades and 3 positions).
  - A fresh instance of Original v3.2 is deployed on Port 8081 with clean $300.00 state.
  - System B (Shadow) is deployed on Port 8084 with clean $300.00 state.
  - Both start forward evaluation at identical $T_0$.
- **Deployment Pattern 2 (In-Place Reset of 8083):**
  - If the user explicitly authorizes clearing Port 8083, state is archived via `V32_ORIGINAL_PRE_RESET_ARCHIVE.tar.gz`, 8083 is reset to $300.00, and Shadow is deployed on Port 8084.
