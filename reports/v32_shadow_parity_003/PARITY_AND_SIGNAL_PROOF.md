# PARITY_AND_SIGNAL_PROOF.md
## Protocol: V32-SHADOW-PARITY-003 — Direct Core Signal Parity & Independent АКПП

- **Project**: TORG_BOT / Bybit Multi-Alpha v3.2 Dual-Regime Shadow
- **Repository**: `samtur513/bybit-alpha-bot-SAM`
- **Branch**: `research/v32-shadow-parity-003`
- **Component**: `v3_2/src/shadow/paper_trader_v32_shadow.py`
- **Verification Suite**: `tests/test_v32_parity_and_execution_proof.py` (100% Pass)
- **Status**: **VERIFIED & AUDIT COMPLETE**

---

## 1. Executive Summary

Under protocol `V32-SHADOW-PARITY-003`, the signal evaluation and risk architecture of `v3_2/src/shadow/` has been completely unified with the production `CoreV3Engine` components. All hand-crafted heuristic signal checks (`close > upper_ch and adx >= 18...`) have been replaced with direct invocations of:
1. `MultiCoinScanner` (`v3/src/multi_scanner.py` / `v3/src/core_v3.py`)
2. `KellyRiskManager` (`v3/src/kelly_sizing.py`)
3. `RegimeGuard` / `NewsConfig` (`v3/src/news_core/`)
4. `MarketRegimeClassifier` (`v3_2/src/regime_classifier.py`)

Simultaneously, complete architectural decoupling between Native Paper (Port 8083 baseline) and Realistic Shadow (Port 8084) risk managers has been enforced. Any divergence in Shadow (such as fee drag, spread gate rejections, or adverse fills) updates **strictly and exclusively** Shadow's internal drawdown and АКПП gear, leaving Native Paper's risk parameters completely uncorrupted.

---

## 2. Direct Original Signal Integration Proof

### 2.1 Elimination of Handcrafted Formulas
In prior iterations, `process_closed_bar_event()` used standalone boolean evaluation:
```python
# DEPRECATED / REMOVED HANDCRAFTED LOGIC:
long_signal = (close > upper_ch) and (adx >= 18.0) and (ema_fast > ema_slow)
short_signal = (close < lower_ch) and (adx >= 18.0) and (ema_fast < ema_slow)
```

In `V32-SHADOW-PARITY-003`, this has been refactored to directly invoke the audited `MultiCoinScanner` from the core engine:
```python
# CURRENT PRODUCTION INVOCATION:
cand: Optional[CoinCandidate] = self.scanner.evaluate_symbol_bar(
    symbol=symbol,
    close=float(close),
    upper_channel=float(upper_ch),
    lower_channel=float(lower_ch),
    ema_fast=ema_fast,
    ema_slow=ema_slow,
    atr=float(atr),
    volume=volume,
    volume_sma=volume_sma,
    adx=adx,
)
```

### 2.2 Mathematical Signal Identity Verification
`MultiCoinScanner.evaluate_symbol_bar()` enforces strict audited quantitative rules:
1. **Donchian Breakout Boundary**:
   $$\text{Long Breakout}: C_t > \max(H_{t-1}, \dots, H_{t-120})$$
   $$\text{Short Breakout}: C_t < \min(L_{t-1}, \dots, L_{t-120})$$
   With strict Point-in-Time zero-lookahead shift `shift(1)`.
2. **Trend Momentum Filter**:
   $$\text{EMA}_{\text{fast}} > \text{EMA}_{\text{slow}} \quad (\text{for Long})$$
   $$\text{EMA}_{\text{fast}} < \text{EMA}_{\text{slow}} \quad (\text{for Short})$$
3. **Volatility Filter**:
   $$\text{ADX}_{14} \ge 18.0$$
4. **Trend Momentum Score (TMS)**:
   $$\text{TMS} = \left(\frac{|C_t - \text{EMA}_{\text{fast}}|}{\text{ATR}_{14}}\right) \times \left(\frac{\text{Volume}}{\text{Volume}_{\text{SMA20}}}\right)$$

In test `test_signal_identity_with_core_v3_scanner`:
- Standalone `CoreV3Engine.scanner` and Shadow Engine's embedded `self.scanner` were supplied identical 1h candle data.
- **Result**: Identical candidate output, direction ($+1$), breakout price ($104.5$), breakout boundary ($103.0$), and Trend Momentum Score ($1.25$).

---

## 3. Decoupled Independent Risk Managers & АКПП (Auto-Gearbox)

### 3.1 Architectural Decoupling Diagram
```
                     +----------------------------------+
                     |    1h Closed Bar Market Feed     |
                     +-----------------+----------------+
                                       |
                     +-----------------v----------------+
                     |    MultiCoinScanner Breakout     |
                     |  (CoreV3Engine Signal Parity)    |
                     +-----------------+----------------+
                                       |
                 +---------------------+---------------------+
                 |                                           |
                 v                                           v
    +-------------------------+                 +-------------------------+
    |   NATIVE RISK MANAGER   |                 |   SHADOW RISK MANAGER   |
    |-------------------------|                 |-------------------------|
    | Base Capital: $300.00   |                 | Base Capital: $300.00   |
    | Taker Fee: 0.090% (MNT) |                 | Taker Fee: 0.100% (VIP0)|
    | Slippage: 0.02% fixed   |                 | Discrete L2 VWAP Ladder |
    | Spread Gate: None       |                 | Spread Gate: 0.08% Max  |
    | АКПП Gear: Native Gear  |                 | АКПП Gear: Shadow Gear  |
    | Drawdown: Native DD     |                 | Drawdown: Shadow DD     |
    | Cooldowns: Native Set   |                 | Cooldowns: Shadow Set   |
    +-------------------------+                 +-------------------------+
```

### 3.2 АКПП State Machine & Downshift Mechanics
The Bybit Multi-Alpha v3.2 АКПП gearbox operates across 3 discrete risk gears:

| АКПП Ступень (Gear) | Drawdown Threshold ($\text{DD}$) | Effective Base Risk | Slot Risk ($1.0\%$ Base) |
| :---: | :---: | :---: | :---: |
| **Gear 3 (Full)** | $\text{DD} < 8.0\%$ | $100\%$ | $1.00\%$ of Equity |
| **Gear 2 (Cautious)** | $8.0\% \le \text{DD} < 15.0\%$ | $50\%$ | $0.50\%$ of Equity |
| **Gear 1 (Defense)** | $15.0\% \le \text{DD} < 30.0\%$ | $25\%$ | $0.25\%$ of Equity |
| **Halt / Kill Switch**| $\text{DD} \ge 30.0\%$ | $0\%$ (Trading Halted) | $0.00\%$ (Emergency Flat) |

### 3.3 Isolation Verification Proof
In test `test_independent_risk_managers_and_gearbox_decoupling`:
1. Both portfolios started at **300.000000 USDT** in **Gear 3**.
2. Shadow portfolio incurred simulated trade losses dropping realized equity to **270.000000 USDT** ($10.0\%$ drawdown from $300.00$ peak).
3. Shadow `KellyRiskManager` evaluated drawdown and automatically shifted from **Gear 3 to Gear 2**:
   - `shadow_gear`: **2**
   - `shadow_risk_pct`: **0.50%** ($50\%$ throttled sizing).
4. Native portfolio maintained realized equity at **300.000000 USDT** ($0.0\%$ drawdown):
   - `native_gear`: **3**
   - `native_risk_pct`: **1.00%** ($100\%$ full sizing).
5. **Divergence Isolation Invariant Upheld**: Zero risk pollution from Shadow to Native.

---

## 4. News Core v1.2 Macro Embargo Guard

Both portfolios share the News Core defensive shield via `RegimeGuard`:
- **Pre-Event Lockout**: 45 minutes prior to High-Impact releases (FOMC, CPI, NFP).
- **Post-Event Lockout**: 30 minutes following release.
- **Signal Gating**: When `macro_blackout_active` is True:
  * Both Native and Shadow reject new entry signals with `EMBARGO_REJECT`.
  * Reason logged to SQLite: `EMBARGO: <EventName>`.
  * Open positions continue following Cascade TP and trailing stops.

---

## 5. Verification Checksums & Test Results

```
============================= test session starts =============================
platform win32 -- Python 3.12.10, pytest-9.1.1, pluggy-1.6.0
collected 53 items

tests\test_v32_parity_and_execution_proof.py .......                     [ 13%]
tests\test_v32_shadow_integrity.py .....                                 [ 22%]
tests\test_v32_shadow_equivalence.py .....                               [ 32%]
tests\test_v32_backup_recovery_safety.py ..                              [ 35%]
tests\test_v32_blocker_repair_manifest.py .....                          [ 45%]
tests\test_v32_dual_paper_preparation.py ......                          [ 56%]
tests\test_v32_legacy_financial_accounting.py .....                      [ 66%]
tests\test_v32_legacy_synthetic_candle_defect.py ....                    [ 73%]
tests\test_v32_legacy_verify_002_invariants.py ......                    [ 84%]
tests\test_v32_legacy_verify_003_cascade_and_pit.py .....                [ 94%]
tests\test_v32_shadow_integrity_manifest.py ...                          [100%]

======================== 53 passed, 1 warning in 4.67s ========================
```
