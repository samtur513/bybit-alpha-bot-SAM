# Forensic Audit of Known Defects in Bybit Multi-Alpha v3.2
## Audit Deliverable 07 — V32-LEGACY-FORENSIC-001

---

## 1. Defect 01: The Artificial OHLC Candle Bug (`open = high = low = close`)

### Forensic Confirmation:
In `v3_2/src/paper_trader_v32.py`, lines 547–554:
```python
next_bars_mock = {
    s: {
        "open": float(open_prices[s]),
        "high": float(open_prices[s]),
        "low": float(open_prices[s]),
        "close": float(open_prices[s]),
    }
    for s, b in bars.items()
}
self.range_engine.step_bar(
    timestamp=ts,
    bars=bars,
    next_bars=next_bars_mock,
    next_open_prices=open_prices,
    ...
)
```

### Mathematical Mechanism of Execution Failure:
Inside `v3_2/src/range_engine.py::evaluate_pending_fills`:
```python
if order.direction == 1:
    # Buy limit fill condition:
    if next_low <= (order.price - pen_buf) and next_open >= order.price:
        filled = True
```
When `next_low == next_open == open_prices[s]`:
- If `open_prices[s] <= (order.price - pen_buf)`, then `next_open < order.price`, so `next_open >= order.price` is **FALSE**.
- If `open_prices[s] >= order.price`, then `next_low <= (order.price - pen_buf)` is **FALSE**.
- **Conclusion:** It is **mathematically impossible** for a Buy Limit or Sell Limit order to fill under this mock candle structure!
- **Consequence:** 100% of Range Maker orders failed to execute. Over the 12-day period, the Range engine recorded **0 fills and 0 closed trades**.

---

## 2. Defect 02: Hardcoded Market Parameter Fallbacks

### Forensic Confirmation:
In `v3_2/src/range_engine.py`, lines 539–541:
```python
ker_val = float(b.get("ker", 0.06)) if "ker" in b else 0.06
crossings_val = int(b.get("crossings", 5)) if "crossings" in b else 5
funding_val = float(b.get("funding_rate", 0.0001)) if "funding_rate" in b else 0.0001
```
Because `prepare_dual_regime_indicators` did not inject `ker`, `crossings`, or `funding_rate` into the bar dictionary, these fallback constants triggered on **100% of live scanning cycles**.

---

## 3. Defect 03: Dual Capital Risk Budget

`PaperTraderV32` initialized both `trend_engine` and `range_engine` with `initial_capital = 300.0`.
- **Architectural Risk:** A shared $300 account could theoretically have been allocated up to $600 if both engines took max concurrent positions.
- **Empirical Reality:** Because the Range engine suffered from Defect 01, it never opened a position. The Trend engine acted as an isolated single-strategy bot with strictly bounded leverage ($< 3.5x$).

---

## 4. Defect 04: PnL & Fee Accounting Integrity

Audit confirmed that `CoreV3Engine` charged:
- **0.100% Taker Fee** on Entry
- **0.100% Taker Fee** on Exit
- **0.020% Slippage** on Entry
- **0.020% Slippage** on Exit
- Realized Partial TPs correctly credited to realized equity.
- Zero double counting of profit.
