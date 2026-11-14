# Forensic Proof of Cascade Take-Profit Execution
## Deliverable 03 — Technical Specification V32-LEGACY-VERIFY-003

- **Audit Target:** 15 Closed Trades with Partial Take-Profit Execution
- **Resolution Level:** 1-Minute Bybit Public V5 Market Klines
- **Classification Standard:** `MARKET_SEQUENCE_VERIFIED` vs `UNRESOLVED_SEQUENCE`

---

## 1. Architectural Execution Mechanism in `CoreV3Engine`

Investigation of `v3_2/src/paper_trader_v32.py` (lines 863–873) revealed the live execution loop:
```python
elif (
    kind == "price"
    and time.monotonic() - received < 5
    and abs(time.time() * 1000 - ts) < 15000
):
    price = Decimal(str(data))
    if price.is_finite() and price > 0:
        self.prices[sym] = price
        self.trend_engine.process_price_event(ts, self.get_live_prices())
```
**Critical Discovery:**
- `process_price_event()` runs on **every incoming WebSocket price tick** (`kind == "price"`), NOT on candle close.
- When market price reaches $P_{entry} - 1.5 \times \text{ATR}$:
  1. `CASCADE_TP1` trims 30% of position quantity immediately.
  2. Stop Loss is ratcheted down to Cash Breakeven ($BE$).
- If market continues trending to $P_{entry} - 3.0 \times \text{ATR}$:
  1. `CASCADE_TP2` trims 40% of position quantity.
  2. Stop Loss is ratcheted down to Secured Profit ($P_{entry} - 1.5 \times \text{ATR}$).
- The remaining 30% runner continues until hitting the trailing stop.

---

## 2. 1-Minute Market Data Sequence Verification

Using authentic 1-minute historical klines directly from Bybit V5 Public API, all 15 partial trades were tested:

### 2.1 Confirmed Sequence Trades (8 Trades — 53.3%):
- **Trades:** `V32_TR_04`, `V32_TR_07` (AKE), `V32_TR_09`, `V32_TR_12` (HYPE), `V32_TR_17`, `V32_TR_19`, `V32_TR_22`, `V32_TR_25` (DOGE).
- **Evidence:** 1-minute klines prove that market price dropped to $TP1$ (and $TP2$) **hours before** the market reversed to hit the trailing stop level:
  - Example `V32_TR_07` (AKE): $TP1$ and $TP2$ reached at 23:53 UTC (Nov 5). Trailing stop hit at 03:11 UTC (Nov 6) — **3 hours 18 minutes later**!
  - Example `V32_TR_12` (HYPE): $TP1$ at 11:26 UTC, $TP2$ at 11:48 UTC, Trailing stop at 14:44 UTC — **2 hours 56 minutes later**!
- **Status:** **`MARKET_SEQUENCE_VERIFIED`**.

### 2.2 Unresolved Sequence Trades (7 Trades — 46.7%):
- **Trades:** `V32_TR_02`, `V32_TR_03`, `V32_TR_11`, `V32_TR_13`, `V32_TR_15`, `V32_TR_21`, `V32_TR_23`.
- **Reason:** In these trades, the final trailing stop price recorded upon exit was touched earlier in the bar history (when the stop was at an initial higher level before ratcheting). Because the bot did not log intermediate trailing stop ratchets to disk, the exact minute-by-minute stop trajectory cannot be verified from state alone.
- **Strict Compliance:** In accordance with the stop-condition rule of Technical Specification V32-LEGACY-VERIFY-003, these 7 trades are classified as **`UNRESOLVED_SEQUENCE`**, NOT PASS.

---

## 3. Financial Attribution of Cascade TP

- **Total Banked Partial Gain across 15 Trades:** **+$27.66 USDT**.
- **Portion Verified by 1m Sequence (8 Trades):** **+$16.66 USDT** (60.2% of partial gain).
- **Portion in Unresolved Sequence (7 Trades):** **+$11.00 USDT** (39.8% of partial gain).
