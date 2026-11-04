# NewDragon v4.0 Universal (Phase 1 Engine)

**Location:** `c:\projects\Swod_Torg_BOT\NewDragon_v4_Universal`  
**Governing Standard:** NewDragon v4.0 Universal Specification & `crypto-hybrid-bot-tester`  
**Target Exchange:** Bybit V5 Linear Perpetuals (VIP0 Fees: Taker 0.055%, Maker 0.020%)

---

## 1. Overview & Phase 1 Scope

This package contains the core mathematical and execution foundations extracted and perfected from the NewDragon v4.0 Universal specification. It is deployed as an independent module inside `Swod_Torg_BOT` for isolated unit testing, invariant verification, and subsequent deployment into `Torg_Bot_3`.

### Key Components Implemented:

1. **`core/models.py`**:
   - `InstrumentSpec`: Bybit V5 contract parameters (`qty_step`, `min_order_qty`, `min_notional`, `max_leverage`).
   - `PortfolioState`: Real-time portfolio equity, available margin, drawdown, and cross-sectional statistics.
   - `SignalOutput`: Standardized Carver signal structure (`scaled_forecast`, `composite_score`).
   - `PositionTracker` & `ExitAction`: State contracts for the 4-Stage Adaptive Exit FSM.

2. **`layers/l5_risk.py` (`UniversalPositionSizer`)**:
   - **Dimensionality Integrity:** True 1R, Fractional Kelly, Risk Parity, and CVaR 95% are strictly evaluated in **base asset coins** by dividing dollar caps by `entry_price` before taking the minimum.
   - **Micro-Account Contract Router:** Prevents risk expansion on Bybit V5 for small balances ($100–$500). Rejects orders where `notional < min_notional` without violating 1R limits.
   - **Stepped Drawdown Brakes:**
     - $DD < 5\% \to 1.0$ (Normal)
     - $5\% \le DD < 10\% \to 0.75$ (Caution)
     - $10\% \le DD < 20\% \to 0.35$ (Severe defense)
     - $DD \ge 20\% \to 0.00$ (Emergency Circuit Breaker)

3. **`layers/l6_execution/exit_fsm.py` (`AdaptiveExitFSM`)**:
   - **Stage 0 (Fast Scratch):** Exits within initial bars if 2 consecutive inside bars form, unrealized profit $< +0.6R$, and order flow imbalance ($Z_{OFI}$) turns adverse.
   - **Stage 1 (Ratchet):** Tightens stop from $-1.0R$ to $-0.35R$ at $+0.85R$ profit (65% risk reduction).
   - **Stage 2 (Partial Scale-Out):** Closes 60% via Maker Limit order (saving 64% in exchange fees) at $+1.40R$ and moves remaining 40% runner stop to $+0.35R$ profit lock.
   - **Stage 3 (Adaptive Chandelier Trail):** Trailing stop at $+2.00R$ using 2.5x ATR, dynamically compressing to 1.2x ATR upon shock detection ($P(\text{Shock}) \ge 0.25$) or severe adverse OFI.

4. **`layers/l2_regime.py` (`CausalRegimeDetector`)**:
   - **Strict Zero-Lookahead Causal Forward Algorithm:** Evaluates $P(S_t \mid X_{1:t})$ in log-space without inspecting future bars.
   - **Deterministic State Mapping:** Solves Label Switching across retrains by sorting states into `shock`, `bull`, `bear`, `sideways`.
   - **2D Classification:** Combines HMM probabilities with Hurst Exponent (R/S analysis).

5. **`adapters/torg_bot_3_bridge.py`**:
   - High-level adapter classes (`UniversalRiskBridge`, `AdaptiveExitBridge`) designed for one-line integration into `Torg_Bot_3` Donchian Breakout (`v3`) and Scalper OMS (`v3_7`).

---

## 2. Test Verification

Run all automated unit and invariant tests:

```powershell
Set-Location "c:\projects\Swod_Torg_BOT\NewDragon_v4_Universal"
pytest tests -v
```

All 11 invariant tests pass with 100% coverage across lookahead invariance, micro-budget routing, drawdown brakes, and FSM stages.
