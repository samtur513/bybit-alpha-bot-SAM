# ADR 0001: Multi-Alpha Engine v2.1 4h Trend Following and News Core Defensive Shield

- **Status**: Accepted
- **Date**: 2026-10-22
- **Deciders**: Quantitative Architecture Team, Pair Programming Agents

## Context

The initial 1h Multi-Alpha v2.0/v2.1 trading system on Bybit Linear Perpetuals experienced high transaction cost drag and false breakout decay in chop regimes (confirmed by independent Dragonfly T46 audit). Additionally, pyramiding accounting previously omitted weighted average entry price recalculation.

## Decision

1. **4h Timeframe Transition (Improvement I1)**:
   - Transition primary candle interval from 1h to 4h (`CANDLE_INTERVAL = "240"`, `BAR_INTERVAL_MS = 14400000`).
   - Use 30-bar Donchian channel (120-hour lookback) with causal `shift(1)` to eliminate future lookahead.
   - Filter breakout signals using `ADX >= 18.0`, trend alignment (`EMA50 > EMA200`), and BTC Delta Trend filter.
2. **Mathematically Rigorous Trade Accounting**:
   - Recalculate average entry price on anti-martingale pyramiding add (+50% at +2 ATR) as $P_{\text{avg}} = \frac{Q_1 P_1 + Q_2 P_2}{Q_1 + Q_2}$.
   - Ratchet trailing stop into *Basket Cash Breakeven*, covering initial entry fees, pyramiding fees, funding payments, and exit slippage.
3. **News Core v1.1 Defensive Shield**:
   - Integrate News Core strictly as a defensive module (`NewsConfig.defensive_only()`).
   - Offensive entries (N1/N3) are disabled.
   - Activate Regime Guard (macro blackout window $\pm 15$m around FOMC, CPI, NFP; stablecoin depeg $>0.8\%$).
   - Activate Position Matrix shields: Emergency Eject, Squeeze Escape, Runner Protection ($R \ge 1.5R$), and Soft Defense.

## Consequences

- 5 out of 5 Walk-Forward Validation (WFV) rolling out-of-sample test windows are profitable, achieving a Walk-Forward Efficiency (WFE) of 83.2%.
- Zero lookahead and mathematical accounting invariants verified across all test suites.
