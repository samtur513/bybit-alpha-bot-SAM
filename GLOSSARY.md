# Quantitative Trading Domain Glossary

Standardized vocabulary and domain concepts for the Bybit Multi-Alpha quantitative trading bot.

## Core Concepts

### Half-Kelly Risk Sizing
A fractional Kelly position sizing model that allocates `BASE_RISK_PCT = 0.02` (2.0%) of current equity per trade. Provides mathematical growth optimization while cutting full-Kelly volatility and drawdowns by ~75%.

### Basket Cash Breakeven
A dynamically computed stop loss level for a pyramided position that guarantees net cash PnL $\ge 0$ upon execution, accounting for:
- Initial entry taker fee
- Pyramiding add taker fee
- Accrued 8h funding payments
- Adverse exit slippage and exit taker fee

### Weighted Average Entry Price
The rigorous post-pyramiding position entry price calculated as:
$$P_{\text{avg}} = \frac{Q_1 P_1 + Q_2 P_2}{Q_1 + Q_2}$$
where $Q_1, P_1$ is the initial position quantity and execution price, and $Q_2, P_2$ is the pyramiding add quantity and execution price.

### Regime Guard
A macroeconomic risk filter within News Core that issues global trading embargoes (`block_new_entries = True`):
- **Macro Blackout**: $\pm 15$ minutes around scheduled high-impact events (FOMC rate decisions, US CPI, US NFP).
- **Stablecoin Depeg**: $> 0.8\%$ deviation of USDC/USDT reference mark from \$1.0000.
- **Safe-Haven Spike**: $> 0.7\%$ 1m surge in gold (PAXG).

### Emergency Eject
A defensive shield in Position Matrix that immediately liquidates an active Long position at market upon a verified `CRASH_SHOCK` (-3) event (protocol exploit, exchange hack, regulatory action, delisting) confirmed by a price drop $\ge 1.0\%$ or Tier-1 official announcement.

### Runner Protection
A risk-preservation rule: if an active Long position has achieved unrealized profit $R \ge 1.5R$, incoming news shock headlines will **not** trigger a panic market dump. Instead, the stop loss is automatically ratcheted to lock in $+0.5R$ profit.

### Squeeze Escape
A defensive shield in Position Matrix that immediately liquidates an active Short position at market upon a `MAJOR_CATALYST` (+3) event (Tier-1 listing, ETF approval, massive buyback).

### Soft Defense
A defensive shield for Tier-3 media rumors arriving without confirmed price panic: tightens the trailing stop loss without dumping the position at market.

### Walk-Forward Efficiency (WFE)
The ratio of annualized Out-Of-Sample (OOS) strategy return to In-Sample (IS) return:
$$\text{WFE} = \frac{\text{Mean OOS Return}}{\text{Mean IS Return}}$$
Values $\ge 50\%$ represent robust, non-overfitted strategies.

### Dynamic Universe Screener
A programmatic quantitative selector that scans all active exchange derivatives contracts (400+ on Bybit) and selects the Top-N assets satisfying liquidity, volatility, trend efficiency, and relative strength thresholds.

### Normalized ATR
The percentage volatility of an asset calculated as:
$$\text{NormATR} = \frac{\text{ATR}_{14}}{\text{Price}} \times 100\%$$
Used to filter out stagnant assets with insufficient range to yield positive $R$-multiples.

### Kaufman Efficiency Ratio (KER)
A metric quantifying the smoothness and directional efficiency of price movement over $n$ periods:
$$\text{KER} = \frac{|\text{Price}_t - \text{Price}_{t-n}|}{\sum_{i=1}^n |\text{Price}_i - \text{Price}_{i-1}|}$$
Values close to 1.0 indicate clean directional trending; values near 0 indicate choppy noise.

### Composite Momentum Score
A multi-factor score used to rank candidates in the universe:
$$\text{Score} = 0.35 \cdot \text{Trend}(\text{ADX}) + 0.30 \cdot \text{RS}(\text{vs BTC}) + 0.20 \cdot \text{VolExpansion} + 0.15 \cdot \text{NormATR}$$
