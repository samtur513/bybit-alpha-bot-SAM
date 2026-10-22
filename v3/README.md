# Bybit Multi-Alpha Quantitative Trading Engine v3.0

> **Dynamic Universe Selection + 4-Hour Donchian Breakout + News Core v1.1 Defensive Shield**  
> Running on **Port 8082** for parallel side-by-side A/B/C testing with v2.0 (8080) and v2.1 (8081).

---

## 1. Overview & Architectural Upgrades in v3

Version 3.0 eliminates static coin list constraints by implementing an institutional-grade **Dynamic Universe Screener** evaluating all 400+ Bybit Linear Perpetuals.

### Key Pillars of v3 Architecture:
1. **Dynamic Universe Screener (`v3/src/universe_screener.py`)**:
   - **Liquidity & Execution Filter**: 24h Turnover $\ge \$25\text{M}$ USDT, Open Interest $\ge \$5\text{M}$ USDT.
   - **Volatility Filter**: Normalized ATR $\ge 2.0\%$ (filters out dead or dormant assets).
   - **Trend Cleanliness**: Kaufman Efficiency Ratio (KER) $\ge 0.30$ or ADX(14) $\ge 16.0$ (filters choppy noise).
   - **Relative Strength**: Outperformance relative to BTC benchmark over 24h/120h.
   - **Funding Rate Hygiene**: Absolute 8h funding rate $\le 0.05\%$ (avoids bleeding yield traps).
   - **Multi-Factor Composite Score**:
     $$\text{Score} = 0.35 \cdot \text{Trend} + 0.30 \cdot \text{RS}_{\text{BTC}} + 0.20 \cdot \text{VolExpansion} + 0.15 \cdot \text{NormATR}$$
   - **Smooth Universe Rotation**: Re-evaluated every 4 hours, guaranteeing that assets with open positions are **never dropped** until closed.

2. **Core 4h Trend Breakout Engine (`v3/src/core_v3.py`)**:
   - Donchian Channel (30 bars = 120 hours) with strict zero-lookahead point-in-time (`high.shift(1)`).
   - BTC Delta Trend Filter (requires BTC bullish alignment for altcoin longs).
   - 3-bar re-entry cooldown on closed symbols/directions.
   - 3.5x portfolio gross leverage cap.

3. **News Core v1.1 Defensive Shield (`v3/src/news_core/`)**:
   - Strictly defensive mode (`defensive_only`), preventing false breakout entries during macro crises.
   - **Regime Guard**: Automatic 30-minute blackout during FOMC / CPI / NFP releases, and automatic freeze upon USDC depeg $> 0.8\%$.
   - **Emergency Eject**: Immediate market close upon verified CRASH_SHOCK events.
   - **Squeeze Escape**: Immediate market close on short positions facing positive super-catalysts.
   - **Runner Protection**: Positions with profit $\ge 1.5R$ trail their stop to lock in profits instead of dumping at market.

4. **Accounting & Mathematical Rigor**:
   - Pyramiding (+50% at +2 ATR) recalculates the exact weighted average entry price.
   - Basket Cash Breakeven stop loss guarantee ($0 net PnL minimum).
   - Bybit Non-VIP 10% MNT taker fee (0.09% / 0.0009).

---

## 2. Directory Structure

```
v3/
├── src/
│   ├── universe_screener.py       # Dynamic screener for 400+ Bybit perpetuals
│   ├── core_v3.py                 # Core quantitative execution & accounting engine
│   ├── paper_trader_v3.py         # Real-time WebSocket daemon
│   ├── config.py                  # Port 8082 configuration & risk constants
│   ├── indicators.py              # Zero-lookahead ATR, ADX, EMA, Channels
│   ├── kelly_sizing.py            # Half-Kelly risk manager & streak recovery
│   ├── market_stream.py           # Robust Bybit WebSocket stream
│   ├── multi_scanner.py           # Multi-asset breakout candidate scanner
│   ├── persistence.py             # Atomic crash-resistant JSON serialization
│   ├── pyramiding_engine.py       # Weighted average anti-martingale logic
│   ├── telegram_notifier.py       # Live event alerts
│   └── news_core/                 # Self-contained News Core v1.1 module
├── tests/
│   ├── test_v3_universe_screener.py
│   ├── test_v3_core.py
│   ├── test_v3_news_defense.py
│   └── test_v3_accounting_invariants.py
├── reports/                       # State, status, and audit reports
├── web_dashboard.py               # Web dashboard (Port 8082) & REST API
├── setup_v3.sh                    # Automated systemd VPS setup script
├── start.sh                       # Start systemd daemon
├── stop.sh                        # Stop systemd daemon
├── restart.sh                     # Restart daemon
├── status.sh                      # Check daemon status
└── logs.sh                        # Live stream journalctl logs
```

---

## 3. Quick Start & Deployment

### Run Locally:
```bash
python v3/web_dashboard.py
```
Open browser at `http://localhost:8082`.

### Run Test Suite:
```bash
pytest v3/tests -v
```

### Deploy to Linux VPS (systemd):
```bash
bash v3/setup_v3.sh
```

---

## 4. REST API Endpoints (Port 8082)

- `GET /api/status`: Real-time portfolio metrics, active positions, countdown timer.
- `GET /api/state`: Full persistent engine snapshot.
- `GET /api/universe`: Current ranked dynamic universe with composite scores and metrics.
- `GET /api/logs`: Live log buffer.
- `POST /api/news_event`: Webhook for incoming breaking news alerts.
