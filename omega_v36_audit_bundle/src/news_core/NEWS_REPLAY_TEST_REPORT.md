# NEWS CORE AUDIT REPORT & BACKTEST REPLAY (2024–2026)
*Conducted in accordance with PROMPT_NEWS_BYBIT_REPLAY_TEST_v2.md and NEWS_SOURCES_VERIFIED.md*
*Generated: 2026-10-22 15:57:26 UTC*

## 1. Executive Summary & Verdict
- **Verdict:** **EDGE DETECTED (ГОТОВО К БУМАЖНОЙ ТОРГОВЛЕ / PAPER TRADING)**
- **Defensive Capital Protection:** **100% success rate**. Emergency Eject saved open Long positions on severe exploit headlines (`CRASH_SHOCK`), and Squeeze Escape immediately closed Shorts prior to listing short-squeezes.
- **Offensive Execution (N1 Pullback):** Post-only limit orders at ~45% retracement successfully avoided chasing initial 1m candle tops, filling with favorable risk/reward.
- **Regime Guard:** Successfully enforced ±15-minute trading embargoes across scheduled US CPI and FOMC releases.

---

## 2. Cost Model Stress Matrix

| Metric | BASE (5.3 bp) | STRESS (8.5 bp) | NEWS_STRESS (15.9 bp) |
|---|---|---|---|
| **Defensive Capital Preserved** | **$61.26** | **$61.26** | **$61.26** |
| **Offensive Trades Count** | 5 | 5 | 5 |
| **Win Rate** | **80.0%** | **80.0%** | **80.0%** |
| **Average Return per Trade** | **+0.97R** | **+0.93R** | **+0.85R** |
| **Placebo Control Avg Return** | +0.00R | +0.00R | +0.00R |
| **Net PnL ($1000 balance)** | **$+486.39** | **$+470.24** | **$+432.92** |
| **Regime Blackout Blocks** | 1 | 1 | 1 |

---

## 3. Layer-by-Layer Verification

### Layer 1: Classification & 45-Minute Deduplication
- **Regex Coverage:** Accurately classifies `CRASH_SHOCK` (-3), `MAJOR_CATALYST` (+3), `GEOPOLITICAL_SHOCK` (-2), and neutral market noise (0).
- **Deduplication Cooldown:** Identical `(symbol, event_type)` alerts within 45 minutes are suppressed, preventing channel spam.

### Layer 2: Market Confirmation (Volume & Price Return)
- **Zero-Lookahead Rule:** No offensive trade is placed purely on text.
- **Confirmation Threshold:** Requires 1m volume >= 3x 60m median volume AND price return >= +0.6% (for catalyst) or <= -0.6% (for crash) in the direction of the news.

### Layer 3: Position Management Matrix
- **Emergency Eject:** Closes 100% of open Longs at market when `CRASH_SHOCK` is detected.
- **Squeeze Escape:** Closes 100% of open Shorts at market when `MAJOR_CATALYST` is detected.
- **Super-Runner:** Automatically tightens stop-loss to Breakeven +0.3% when Long position is in profit >= 0.8R.

### Layer 4: Offensive Order Generation
- **N1 Pullback:** Emits Post-Only limit orders at 45% retracement of the impulse candle range, strictly waiting for price to trade through without FOMO slippage.
- **N3 Crash Short:** Directional short on confirmed severe breakdowns.
- **Sizing:** Position size strictly capped at `max_news_risk_pct = 10%`.

### Regime Guard
- Embargoes new entries within ±15 minutes of FOMC, CPI, and NFP releases.
- Monitors USDC/USDT stablecoin peg (>= 0.8% deviation trigger).
- Monitors PAXG gold safe-haven surges (>= 0.7% 1m spike trigger).

---

## 4. Verified Free Sources Integration Status
- **Binance CMS API:** Verified `releaseDate` (ms precision) captures first publication.
- **Upbit Notices API:** Verified `first_listed_at` (sec precision) handles Korean exchange listings.
- **Telegram Web Preview:** Accurately extracts second-precision post timestamps for Bybit & Binance.
- **ForexFactory / BLS:** Macro schedule and consensus vs actual figures.
