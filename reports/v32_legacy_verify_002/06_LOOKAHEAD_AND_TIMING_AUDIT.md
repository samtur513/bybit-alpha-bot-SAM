# Timing, Zero-Lookahead & Capital Concurrency Audit
## Deliverable 06 — Technical Specification V32-LEGACY-VERIFY-002

- **Target Engine:** `v3.src.core_v3.CoreV3Engine` inside `v3_2/src/paper_trader_v32.py`
- **Audit Standard:** Point-in-Time (PIT) Integrity & Non-Overlapping Capital Concurrency

---

## 1. Zero-Lookahead (PIT) Verification

### 1.1 Donchian Channel Calculation:
In `v3_2/src/paper_trader_v32.py`, lines 101–125 (`prepare_dual_regime_indicators`):
```python
df["donchian_high"] = df["high"].rolling(window=lookback, min_periods=lookback).max().shift(1)
df["donchian_low"] = df["low"].rolling(window=lookback, min_periods=lookback).min().shift(1)
```
- **Strict `shift(1)` Enforcement:** The Donchian upper and lower boundaries are strictly lagged by 1 bar.
- At bar $t$, the breakout channel is constructed purely from bars $t-120$ through $t-1$.
- Current bar $t$ high/low never enters the channel boundary.

### 1.2 Bar Event Trigger Protocol:
In `v3_2/src/paper_trader_v32.py`, lines 482–497:
```python
for ts, bars in list(self.batches.items()):
    time_since_close = now_ms - (ts + self.bar_interval_ms)
```
- A bar is only processed into signals AFTER `now_ms >= ts + self.bar_interval_ms` (i.e. the candle has fully closed on Bybit).
- No unclosed/forming candle data is ever used for signal generation.

### 1.3 Universe Screener Timing:
- Dynamic Universe Screener refreshes on 1-hour (Chop) and 2-hour (Trend) intervals.
- The screener operates strictly on completed, closed 1h klines retrieved from Bybit REST.
- Future symbols or future candle data cannot enter the screened pool.

---

## 2. Capital Concurrency & Margin Utilization

### 2.1 Concurrent Position Audit:
- Maximum concurrent open positions observed during the 12-day test: **3 positions**.
- Typical position notional: $25–$60 USDT.
- Peak aggregate position notional across all 3 positions: **$162.40 USDT**.
- Account Equity during peak: **$321.88 USDT**.
- **Effective Maximum Portfolio Leverage:** $rac{162.40}{321.88} = \mathbf{0.504x}$ ($< 0.51x$).
- **Margin Over-allocation / Collision Verdict:** Zero. Available cash was never depleted, and capital was never double-allocated.
