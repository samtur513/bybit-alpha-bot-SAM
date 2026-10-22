**Deep Research Report: Universal Configurable Crypto Trading System**

**1. Coin Screening & Selection**

**Quantitative Filtering Pipeline**

Modern institutional crypto screening uses a sequential multi-factor pipeline:

\[ Universe: Top 200–500 Coins by Market Cap \]

     	│

     	▼

\[ 1. Volume Filter \] ──> Remove low-liquidity assets (ADT < 10M, RVOL check)

     	│

     	▼

\[ 2. Volatility Filter \] ──> Eliminate extreme tail-risk regimes & apply vol scaling

     	│

     	▼

\[ 3. Momentum Filter \] ──> Rank survivors via Z-Score / TSMOM / CSMOM signals

     	│

     	▼

\[ Final Portfolio Construction \] ──> Dynamic Long/Short or Long-Only Rebalancing

**Key Formulas**

**Relative Volume (RVOL) Filter:**

RVOL\_t = Volume\_t / (1/N × Σ Volume\_{t-i})

Screening Rule: RVOL\_t > 1.5

**Volatility-Scaled Momentum:**

Scaled\_Return\_t = R\_t / σ\_{t,window}

Divides raw returns by rolling volatility (Garman-Klass or rolling std) so hyper-volatile altcoins don't dominate.

**Z-Score Momentum Strategy:**

Z = (Rolling\_Mean(Short\_Window) - Rolling\_Mean(Long\_Window)) / Std\_Dev(Long\_Window)

Selection Rule: Rank universe by Z-score, go long top deciles

**Critical Findings**

- **Large-cap tokens** exhibit stable momentum; **small-cap tokens** heavily mean-revert
- Volume-weighted momentum significantly stabilizes out-of-sample performance
- Standalone momentum yields heavy tail risk; combining with volume + volatility filter drastically improves Sharpe and Calmar ratios
- Minimum Average Daily Turnover (ADT) cutoff of 10M prevents slippage issues

---

**2. News & Sentiment Analysis**

**Models & Architecture**

| **Model** | **Type** | **Strengths** |
| --- | --- | --- |
| **FinBERT** | Domain-specific BERT | Financial text; positive/negative/neutral classification |
| **CryptoBERT** | Crypto-specific BERT | Crypto slang, Reddit/Twitter jargon |
| **GPT-4/Claude** | General LLM | Complex context, sarcasm, regulatory statements |
| **DeBERTa+RoBERTa+FinBERT ensemble** | Ensemble | Highest robustness for directional prediction |

**Data Sources**

**CryptoPanic** — News aggregator with REST API, bullish/bearish classification, community voting metrics. Best for event-driven bots.

**LunarCrush** — Social media tracking (X, Reddit, YouTube). Proprietary Galaxy Score™ & AltRank™. Best for spotting retail FOMO/panic.

**Santiment** — Deep on-chain + social analytics. Weighted Sentiment, exchange inflows/outflows, whale movements, dev activity (GitHub commits). Best for macro swing trading.

**4-Step Sentiment Pipeline**

- **Ingest** — CryptoPanic + social feeds + GDELT
- **Preprocess** — Remove spam, chunk into headline+summary, normalize
- **Score** — FinBERT/CryptoBERT → P\_pos, P\_neg, P\_neu with sliding window aggregation (1h, 4h, 24h)
- **Signal** — Threshold or hybrid (combine with MACD/RSI) for execution

**News Propagation Speed & Trading Windows**

| **Time After Event** | **Activity** | **Trading Implication** |
| --- | --- | --- |
| **T+0 to <1s** | HFT NLP bots parse headlines | Machine-only alpha |
| **T+1 to 5s** | Cross-exchange arb bots align prices | Latency arb window closes |
| **T+30s to 3min** | Retail influx, volatility expansion | Momentum entry window |
| **T+5 to 15min** | Price discovery stabilizes | Most headline alpha priced in |

**Key Finding:** For major news, directional price impact peaks at **2–5 minutes** post-release. Beyond 15 minutes, public headline alpha is fully priced in. Altcoins show slower sentiment propagation (hours/days) vs BTC/ETH.

**Practical Considerations**

- **Latency:** Heavy LLMs need quantization (GGUF, TensorRT) or distilled models for real-time
- **Manipulation:** Source credibility weighting needed to filter pump-and-dump bot armies
- **Asset Specificity:** Altcoins (especially DeFi tokens) show stronger sentiment-price correlation than BTC

---

**3. Entry/Exit Signal Systems**

**Multi-Timeframe Architecture**

Split analysis into three tiers:

- **Macro/Trend (HTF — 4H/1D):** Structural bias (Bullish/Bearish/Range)
- **Intermediate/Momentum (ITF — 15m/1H):** Momentum shifts and structural swings
- **Micro/Execution (LTF — 1m/5m):** Order book pressure, liquidity imbalances

**Composite Scoring Engine**

Composite\_Score = W1(S\_HTF\_MACD) + W2(S\_HTF\_RSI) + W3(S\_ITF\_Confluence) + W4(Z\_OFI) + W5(VD)

**Suggested Weights:**

- W1 (HTF MACD Trend): **0.25**
- W2 (HTF RSI Regime): **0.15**
- W3 (ITF Momentum/Crosses): **0.20**
- W4 (Microstructure OFI): **0.25**
- W5 (Volume Delta): **0.15**

**Execution Thresholds:**

- Strong Long: Score ≥ +0.65
- Strong Short: Score ≤ −0.65
- Neutral/Flat: −0.30 < Score < +0.30

**Key Microstructure Signals**

**Order Flow Imbalance (OFI):**

OFI = ΔBid\_Volume − ΔAsk\_Volume

Normalize via rolling Z-scores; Z\_OFI > +1.5 = aggressive buying

**Volume Delta (VD):**

VD = (Σ Vol\_buyer\_initiated − Σ Vol\_seller\_initiated) / Σ Total\_Vol

**Mean Reversion vs Momentum Decision Framework**

| **Regime** | **Indicator Signature** | **Strategy** |
| --- | --- | --- |
| **Trending** | Hurst H > 0.55; High Volume; Positive Drift | **Momentum** |
| **Ranging** | Hurst H < 0.45; Low/Compressing Vol | **Mean Reversion** |
| **Transition** | Hurst H ≈ 0.5; Vol Spikes | **Cash / Neutral** |

**Key Insight:** Crypto displays **short-term momentum** (1–4 weeks) alongside **pronounced long-term reversals**. Static adherence to either style is suboptimal. Use rolling Hurst Exponent as the regime switch.

---

**4. Position Sizing & Risk Management**

**Kelly Criterion**

**Classic Kelly (binary outcomes):**

f\* = (p × b − q) / b

where p = win rate, q = 1−p, b = reward/risk ratio

**Continuous Trading Kelly:**

f\* = μ / σ²

where μ = expected return, σ² = variance

**Fractional Kelly (CRITICAL for crypto)**

f\_safe = f\* × K\_frac	(K\_frac typically 0.25 to 0.5)

Full Kelly is **dangerous in crypto** due to fat-tailed distributions and estimation error. Half-Kelly captures \~75% of theoretical growth with drastically reduced ruin probability.

**ATR-Based Dynamic Position Sizing**

Stop\_Distance = n × ATR\_14	(n = 2 or 3)

Position\_Size (Units) = (Total\_Capital × f\_safe) / (ATR\_Distance × Price)

**Risk Parity Integration (Multi-Asset Portfolio)**

Risk\_Weight\_i = (1/σ\_i) / Σ(1/σ\_j)

Prevents high-beta altcoins from dominating portfolio risk. If Kelly suggests size exceeding Risk Parity budget, cap at the RP limit.

**Maximum Drawdown Control**

**Mechanical Drawdown Brakes:**

- DD hits **−10%**: Reduce risk budget, cut altcoin allocations by 25%
- DD hits **−20%**: Force defensive posture (50%+ cash/stablecoins)

**Advanced Frameworks:**

- **CVaR (Conditional Value-at-Risk)** constraints instead of variance
- **Calmar Ratio** optimization (CAGR / Max Drawdown)
- **Bayesian GMV portfolios** — use MCMC sampling for weight distributions instead of point estimates
- **Ledoit-Wolf shrinkage** on covariance matrix to prevent inversion errors from high altcoin multicollinearity

**Correlation-Adjusted Sizing**

- Realize that crypto correlations spike to \~1.0 during crashes, breaking standard Markowitz diversification
- Apply dynamic volatility caps: if 30-day rolling vol spikes past threshold, mechanically scale down max weight
- Hold 30-40% stablecoins as a structural circuit breaker

---

**5. Market Regime Detection**

**Hidden Markov Models (HMMs)**

**3-4 State System:**

- **Bull** — Positive drift, low-moderate vol → Trend-following, momentum
- **Bear** — Negative drift, high vol → Short or flat/stablecoins
- **Sideways** — Near-zero drift, low vol → Grid trading, mean reversion
- **High-Vol Shock** — Volatility clustering state → Halt directional exposure

**Implementation (Python):**

python

from hmmlearn.hmm import GaussianHMM

\# Features: log returns + rolling 14-day volatility

X = df\[\["returns", "volatility"\]\].values

model = GaussianHMM(n\_components=3, covariance\_type="full", n\_iter=1000, random\_state=42)

model.fit(X)

regimes = model.predict(X)

**Key Output — Transition Probability Matrix:** Bull and Bear regimes exhibit high persistence. Sideways acts as transitional buffer with higher transition probabilities toward both extremes.

**Adaptive Strategy Routing**

If State = Bull	→ Engage Trend-Following Engine

If State = Sideways → Switch to Mean-Reversion/Grid Engine

If State = High-Vol → Halt directional exposure, maximum risk reduction

**Advanced Regime Tools**

- **Markov-Switching GARCH (MSGARCH)** — Models structural breaks and time-varying volatility
- **SETAR Models** — Threshold mean reversion with regime-dependent asymmetry
- **Hurst Exponent** — Primary filter: H > 0.5 = trending, H < 0.5 = mean-reverting
- **Hybrid K-Means + HMM** — Pre-cluster high-frequency features before feeding to HMM transition matrix

---

**6. AI/ML Integration**

**Reinforcement Learning**

**PPO (Proximal Policy Optimization)** — Preferred for crypto due to clipped surrogate objective preventing destructive policy updates. Empirically outperforms A2C and DQN in volatile crypto benchmarks.

**A2C (Advantage Actor-Critic)** — Faster iteration but more sensitive to hyperparameters.

**Reward Function Engineering (Critical):**

- Don't use raw PnL (encourages reckless leverage)
- Use Sharpe-penalized rewards: penalize drawdowns, high volatility, excessive transaction costs
- Add trend-alignment penalties (punish going long against 200-EMA trend)

**Transformer Architectures**

- **GTrXL (Gated Transformer-XL)** as input encoder for PPO agents — resolves vanishing gradients in long sequences
- Attention mechanisms dynamically weight historical events (volume spike 3h ago > routine price action yesterday)

**Feature Engineering Best Practices**

- **Raw data:** OHLCV, order book snapshots, liquidation data
- **Technical indicators:** EMA, RSI, MACD, Bollinger Bands
- **Pre-trained temporal models:** LSTM/lightweight Transformer for multi-step return prediction
- **State space:** Normalized lookback window (168 steps) + account metrics (balance, unrealized PnL, leverage)

**LLM-as-Judge Architecture**

\[ Technical Models (TA) \] ──┐

\[ Fundamental Models (FA)\] ──┼──> \[ Sub-Model Signals & Reasoning \]

\[ Sentiment Models (SA)  \] ──┘                              	│

                                      	                       ▼

                                                    \[ LLM-as-a-Judge Agent \]

                                                    (Contextual Meta-Reviewer)

                                                                 │

    	                                                         ▼

                                                    \[ Risk Guardrails & API \]

**Key Insights:**

- Stronger LLMs can underperform simpler models by over-weighting factual data and missing narrative momentum
- During bull phases: subjective sentiment signals yield higher returns
- During bear phases: factual/fundamental + technical signals protect capital
- Standard practice: deterministic Python handles math; LLM handles qualitative weighting and risk narrative

**Frameworks & Libraries**

- **FinRL** — Leading open-source DRL framework for financial markets (PPO, DDPG, SAC agents)
- **Stable-Baselines3** — Scalable RL implementations
- **Ray RLlib** — Distributed rollout workers for Transformer training
- **VectorBT** — Lightning-fast vectorized backtesting
- **CCXT** — Universal exchange connectivity (100+ exchanges)
- **Freqtrade** — Production-ready bot with built-in backtesting, FreqAI ML module, dry-run → live pipeline

---

**7. Scalability & Budget Universality**

**Scale-Dependent Constraints**

| **Capital** | **Primary Bottleneck** | **Execution Style** | **Fee Focus** |
| --- | --- | --- | --- |
| **$100** | Exchange minimums (10 min notional) & gas fees | Market orders (taker) | Avoid flat/network fees |
| **50K** | Slippage on altcoins, fee drag | Transition to maker (Post-Only) | Capture maker rebates |
| **$1M+** | Liquidity depth, market impact, adverse selection | TWAP/VWAP, Iceberg, SOR, OTC | VIP tiers, negative maker fees |

**Slippage Model (Square-Root / Linear Impact)**

Slippage (bps) = k × (Order\_Size / ADV\_or\_Book\_Depth)^α

where α ∈ \[0.5, 1.0\] depending on volatility

**Advanced Execution for Large Accounts**

- **TWAP/VWAP:** Break $250K into $1K chunks every 2-5 seconds
- **Iceberg Orders:** Show only fraction of true size
- **Smart Order Routing (SOR):** Split across Binance, Bybit, OKX, Coinbase
- **OTC/RFQ Desks:** For bulk entries bypassing public books

**Fee Optimization**

- **Bybit VIP0:** Taker 0.055%, Maker 0.020%
- Moving to VIP tiers drops taker to \~0.02% and may yield **negative maker fees (rebates)**
- High-frequency strategies must pivot from taker to passive maker + Post-Only limits

---

**8. Backtesting Best Practices**

**Walk-Forward Optimization (WFO)**

- **In-Sample (Train):** Optimize parameters over fixed window (e.g., 12 months)
- **Out-of-Sample (Test):** Forward-test on subsequent unseen window (e.g., 3 months)
- **Roll Forward:** Shift window and repeat
- **Aggregate:** Stitch together all OOS segments for realistic equity curve

**Advanced:** Use **Combinatorial Purged Cross-Validation (CPCV)** — respects market regime transitions in splits.

**Lookahead Bias Prevention**

- Normalization params (mean, std) must be computed **rolling or in-sample only**
- Signal on bar T → execute at Open of T+1 (not close of T)
- Account for survivorship/delisting bias in altcoin universes
- Never apply global min-max scaling across entire dataset before train/test split

**Monte Carlo Simulation**

- **Block Bootstrap:** Randomly reshuffle blocks of historical OOS trades to test sequence sensitivity
- **Drawdown Distribution:** Generate 1000s of alternate equity paths → compute 95th percentile max drawdown and probability of ruin
- **Noise Injection:** Perturb entry/exit prices with random noise to verify edge isn't dependent on exact fills

**Transaction Cost Modeling Checklist**

- ✅ Maker/Taker fees (tier-dependent, 0.02%–0.10%)
- ✅ Dynamic slippage (ATR-based or volume participation rate)
- ✅ Funding rates for perpetual futures (critical — can flip profitable carry strategies to losers)
- ✅ Execution lag modeling (realistic fill simulation)
- ✅ Gas fees for DEX strategies

**Complete Backtest Pipeline**

1. Point-in-time data with delistings/forks handled

2. Signal on T → Execute at Open(T+1)

3. Strict WFO: optimize in-sample only, evaluate OOS only

4. Full friction: fees + variable slippage + funding rates

5. Monte Carlo validation: confidence intervals for DD and returns

---

**Summary of Key Architectural Recommendations**

- **Layer 1 — Universe Screening:** Volume + Volatility + Momentum sequential pipeline with $5M+ ADT floor
- **Layer 2 — Regime Detection:** 3-4 state HMM (or Hurst Exponent) driving strategy router
- **Layer 3 — Signal Generation:** Multi-timeframe composite scorer with configurable weights per regime
- **Layer 4 — Sentiment Overlay:** FinBERT/CryptoBERT + CryptoPanic/LunarCrush/Santiment as confirmation gate
- **Layer 5 — Position Sizing:** Fractional Kelly × ATR-based sizing × Risk Parity constraints
- **Layer 6 — Risk Management:** Mechanical drawdown brakes, correlation-adjusted caps, CVaR constraints
- **Layer 7 — AI Meta-Layer:** LLM-as-Judge for signal arbitration + PPO agents for adaptive execution
    - **Layer 8 — Validation:** Walk-forward optimization + Monte Carlo stress testing + full transaction cost modeling

 **Comprehensive Codebase Analysis: NewDragon / Crypto-Accelerator v3.1**

I've thoroughly read every source file, all documentation, strategy modules, tests, scripts, data directories, and extensions. Here is the full picture:

---

**1. DIRECTORY STRUCTURE**

c:\projects\New2\_Dragonflay\

├── src/              	        \# Core Python packages

│   ├── config.py             	\# Configuration, fees, risk profiles, instrument specs

│   ├── models.py             	\# Data models (Bar, Signal, Order, Position, Trade)

│   ├── data\_loader.py        	\# Bybit V5 REST kline fetcher + synthetic data generator

│   ├── indicators.py         	\# Vectorized indicators (EMA, ATR, BB, KC, EWMAC, ER, ADX, etc.)

│   ├── risk\_engine.py        	\# True 1R position sizing, circuit breakers, loss limits

│   ├── execution\_engine.py   	\# Fill simulation, stop management, two-stage exit FSM

│   ├── backtester.py         	\# 917-line event-driven multi-asset backtest engine (THE core)

│   ├── news\_engine.py        	\# NENE v2.0 - Active news trading (N1/N2/N3 scenarios)

│   ├── news\_regime\_guard.py  	\# Macro embargo, black swan kill, geopolitical filter

│   ├── macro\_calendar.py     	\# US CPI/FOMC/NFP schedule 2022-2026 with UTC timestamps

│   ├── durable\_db.py         	\# SQLite WAL intent journal, execution fills, risk state persistence

│   ├── reconciler.py         	\# Exchange state reconciliation, fail-closed stop verification

│   ├── evaluator.py          	\# Deflated Sharpe Ratio, rolling 105-day cohort evaluation

│   ├── observability.py      	\# Structured JSON logging, health checks, alert dispatch

│   ├── run\_ablation\_study.py 	\# Ablation study runner

│   ├── telegram\_parser.py    	\# Telegram message parsing

│   ├── modules/

│   │   ├── vcb\_runner.py     	\# Strategy A: Volatility Compression Breakout (1h)

│   │   ├── cf\_tsmom.py       	\# Strategy C: Carver-style EWMAC momentum (4h)

│   │   ├── liquidation\_reversal.py \# Strategy B: Liquidation Cascade Reversal (5m)

│   │   └── baselines.py      	\# Benchmark strategies (Donchian-20, EMA 50/200)

│   └── extensions/

│   	├── funding\_basis.py  	\# READ\_ONLY funding rate/basis scanner

│       └── solana.py         	\# DISABLED Solana DEX adapter

├── tests/ 	                   \# 9 test files, 77+ tests total

│   ├── test\_core.py

│   ├── test\_news\_engine.py

│   ├── test\_redteam.py       	\# Round 1 red-team

│   ├── test\_independent\_redteam\_round2.py

│   ├── test\_independent\_redteam\_round3.py

│   ├── test\_r3\_deep\_invariants.py

│   ├── test\_r31\_fail\_closed.py   \# Round 3.1 regression

│   ├── test\_mock\_exchange\_integration.py

│   └── test\_tz\_v31\_qa.py

├── docs/

│   ├── PROJECT\_STATE.md      	\# Module inventory (M01-M24), capital ledger, audit summary

│   ├── IMPLEMENTATION\_PLAN.md	\# 4-week plan, gate definitions (G0-G6)

│   ├── EXPERIMENTS.md        	\# Hypothesis registry, backtest results, ablation matrix

│   ├── DECISIONS.md       	   \# 12 ADRs covering all architectural choices

│   └── RUNBOOKS.md           	\# 5 emergency playbooks, operating modes

├── scripts/

│   ├── download\_bybit\_data.py	\# Bybit kline downloader

│   ├── download\_bybit\_oi.py  	\# Open interest downloader

│   ├── download\_extra\_universe.py

│   ├── audit\_system.py       	\# Full system audit (37 parquet files QC)

│   ├── economic\_breakeven.py 	\# Break-even analysis for $1.50 True 1R

│   ├── kill\_switch.py        	\# Emergency PAUSE / EMERGENCY\_CLOSE\_ALL

│   ├── run\_baselines\_comparison.py

│   ├── run\_active\_news\_backtest.py

│   ├── run\_news\_ablation\_study.py

│   ├── run\_feb2025\_hack\_ablation.py

│   ├── analyze\_period.py / analyze\_all\_periods.py

├── data/

│   ├── klines/               	\# 4 stress periods × 6 assets = 24 parquet files

│   │   ├── svb\_mar2023/      	\# SVB Collapse

│   │   ├── geopol\_apr2024/   	\# Middle East geopolitics

│   │   ├── macro\_nov2024/    	\# US Elections

│   │   └── feb2025\_eth\_hack/ 	\# Bybit Hack

│   ├── bybit/                    \# 13 continuous Bybit parquet archives (multi-year)

│   ├── telegram/             	\# bwenews\_ru.json, bybit\_announcements.json

│   └── data\_manifest.json    	\# SHA-256 manifest for all 37 parquet files

├── main.py              	     \# CLI entry point (backtest / fetch modes)

├── AGENTS.md                 	\# Agent guidelines

└── pytest.ini                	\# Test configuration

---

**2. CORE MODULES — DETAILED BREAKDOWN**

**2.1 Configuration (src/config.py — 179 lines)**

    - **RunMode enum**: OFF, READ\_ONLY, HISTORICAL\_REPLAY, SHADOW, TESTNET, LIVE\_MICRO, LIVE\_LIMITED
    - **Current mode**: READ\_ONLY (hardcoded, live strictly off)
    - **Fee constants**: Bybit VIP0 — Taker 5.5 bps, Maker 2.0 bps, Slippage 3.0 bps base / 6.0 bps stress
    - **Capital**: 180 CEX, $60 Solana locked, $60 reserve)
    - **Risk limits**: $1.50 True 1R per trade, 1 max position, 2x max leverage, $9/day loss limit, 150 hard stop
    - **4 risk profiles**: LIVE\_MICRO\_CEX (canonical), SHADOW, BASE, AGGRESSIVE, EXTREME
    - **Instrument specs**: 12 Bybit Linear USDT perpetuals (BTC, ETH, SOL, XRP, DOGE, AVAX, LINK, ADA, DOT, NEAR, LTC, SUI) with qty\_step, min\_qty, price\_step, min\_notional
    - **Asset universe**: PRIMARY = \[ETH, SOL, XRP\], BLIND\_HOLDOUT = \[DOGE, AVAX, LINK, ADA, DOT, NEAR, LTC, SUI\]

**2.2 Data Models (src/models.py — 172 lines)**

    - Bar: timestamp, OHLCV, open\_interest, funding\_rate
    - Signal: symbol, module\_name, side, entry/stop/TP prices, quality\_score, time\_stop\_bars, metadata
    - Order: full order lifecycle with TTL, partial fill tracking
    - Position: 23 fields including stage tracking (0→1→2→3), consecutive\_inside\_bars, max\_unrealized\_r, accumulated\_funding/fees
    - Trade: completed trade record with gross/net PnL, fees, funding, r\_multiple, exit\_reason

**2.3 Risk Engine (src/risk\_engine.py — 290 lines)**

    - **True 1R formula**: L\_unit = |P\_e - P\_s| + slippage + fees + funding\_buffer, then Q = floor(R\_budget / L\_unit / qty\_step) \* qty\_step
    - **Hard rejection** if notional < minNotional (never expands risk)
    - **Circuit breakers**: Daily $9, module $90, lifetime $60 pause / $150 stop
    - **Kill switch**: checks .system\_pause.lock file
    - **Risk state persistence** via SQLite WAL (survives restarts)
    - **House-Money scaling** for legacy profiles (profit expansion up to max\_risk\_pct)
    - **Liquidation safety check**: stop distance vs estimated liquidation distance

**2.4 Execution Engine (src/execution\_engine.py — 315 lines)**

    - **Fill simulation**: Market (bar open ± slippage), Limit Post-Only (queue penalty trade-through), Stop-Market (conservative gap fill)
    - **Two-stage exit FSM**:
        - Stage 0→1: At +0.85R, ratchet stop from -1R to -0.35R
        - Stage 1→2: At +1.4R, close 60% at maker fee, lock remaining at +0.35R
        - Stage 2→3: At +2.0R, activate Chandelier trailing stop (2.5 × ATR\_1h)
    - **Fast-scratch exit**: VCB breakouts failing within first 12 bars (2 consecutive inside-channel closes and max\_unrealized\_r < 0.6R)
    - **Conservative gap-through-stop**: fills at min(bar.open, stop\_price) for longs, max() for shorts

**2.5 Backtester (src/backtester.py — 917 lines) — THE CENTRAL ENGINE**

    - **Event-driven**: unified chronological timeline across all symbols
    - **Vectorized indicator pre-computation**: resamples 5m→1h→4h with strict zero-lookahead (shift(1) on all indicators)
    - **Strategy signal generation** integrated directly (not via module.generate\_signal() — the modules exist but backtester has its own inline logic):
        - VCB\_RUNNER: checked on 1h close (minute 55), requires squeeze ≥6/12, BBW% ≤15, volume ≥1.5x, ER ≥0.16, 4h slope alignment
        - CF\_TSMOM: checked on 4h close, requires |forecast| ≥8.5, 4h EMA alignment
        - LIQUIDATION\_REVERSAL: checked on 5m bars, requires ≥3% flush, vol\_z ≥2.5, OI drop ≤-1.5%
        - BENCH\_DONCHIAN: 20-period channel breakout on 1h
        - BENCH\_EMA50\_200: Golden/Death cross on 4h
    - **Active News Engine (NENE v2.0)**: ingests Telegram archives, creates watches, executes N1 VWAP pullback, N2 sympathy spillover, N3 crash breakdown
    - **Joint Position Management**: emergency ejects, super-runner boosts, pyramid additions, slot pre-emption
    - **Cross-asset regime**: uses USDC price (depeg detection), BTC 15m return, PAXG 15m return as regime sensors
    - **Report generation**: comprehensive metrics including Sharpe, Calmar, profit factor, fees, funding, news trade stats

---

**3. EXISTING STRATEGIES & SIGNALS**

| **Strategy** | **Timeframe** | **Entry Logic** | **Exit Logic** | **Status** |
| --- | --- | --- | --- | --- |
| **VCB\_RUNNER (A1)** | 1h | BB squeeze inside KC ≥6/12 bars + BBW%≤15 + vol≥1.5x + ER≥0.16 + 4h slope≥0.15 + bar\_pos≥0.65 | Fast-scratch (2 inside bars <0.6R), Stage ratchets, TP1 60%@+1.4R, Chandelier trail | ACTIVE |
| **CF\_TSMOM (B1)** | 4h | Multi-horizon EWMAC ensemble (16/64, 32/128, 64/256) with | forecast | ≥8.5 + 4h EMA alignment |
| **LIQ\_REVERSAL (C1)** | 5m | 15m drop ≤-3% + vol\_z≥3.5 + OI drop ≤-1.8% + green bar | Post-only maker entry, standard exits | PENDING (needs live OI) |
| **NEWS\_N1** | 5m | Post-news VWAP 45% pullback, impulse +1.2%..+9.0% | Limit post-only, 25min TTL | PROPOSED |
| **NEWS\_N2** | 5m | Sympathy spillover (beta-adjusted lag≥2%, vol≥2x) | Limit post-only, 45min TTL | PROPOSED |
| **NEWS\_N3** | 5m | Crash breakdown short (-0.7%..-8.0%) on verified hack/delist | Stop-market, 3hr TTL | PROPOSED |
| **BENCH\_DONCHIAN** | 1h | 20-period channel breakout | Stop-market, no filters | BENCHMARK |
| **BENCH\_EMA50\_200** | 4h | 50/200 EMA golden/death cross | 4% stop, 8% target | BENCHMARK |

**Ablation results (VCB\_A1)**:

    - Remove squeeze: trades +85%, return -4.2%, DD 2.6%→6.8% → **KEEP**
    - Remove ER\_168: trades +40%, return -1.9% → **KEEP**
    - Remove 4h slope: trades +30%, return -2.8% → **KEEP**
    - Remove fast-scratch: net return -1.1%, DD +1.5% → **KEEP**

---

**4. DATA PIPELINE**

**Current state — BATCH ONLY, no live streaming:**

    - **Data ingestion**: BybitDataLoader class fetches klines via Bybit V5 REST API (GET /v5/market/kline), also fetches funding rate history
    - **Local storage**: Apache Parquet via PyArrow for high-throughput replay
    - **Data assets available**:
        - data/klines/: 4 stress periods × 6 assets = 24 parquet files (5m OHLCV)
        - data/bybit/: 13 continuous parquet archives (BTC, ETH, SOL, XRP, DOGE, AVAX, LINK, NEAR, SUI, PAXG spot, USDC spot, + funding files)
        - data/telegram/: 2 Telegram archive JSONs (BWE News, Bybit Announcements)
    - **Synthetic data generator**: deterministic 5m bars with volatility clustering, liquidation cascades, funding cycles
    - **Data QC**: NaN scrub, monotonicity check, OHLC invariants, SHA-256 manifest verified for all 37 files
    - **Clock invariants**: strict point-in-time — signals generated AFTER bar close + delivery lag, no shift(-1)

**MISSING (planned for Week 3)**:

    - ❌ Public WebSocket listener (trades, top-of-book, L2 orderbook)
    - ❌ Real-time streaming data pipeline
    - ❌ Shadow execution harness for virtual orders at live prices

---

**5. RISK MANAGEMENT — COMPREHENSIVE**

The risk engine is **the most battle-tested component**, having survived 4 rounds of independent red-team audits:

    - **True 1R sizing**: accounts for entry fee + stop fee + slippage + funding buffer
    - **Single slot constraint**: max 1 concurrent position (pilot mode)
    - **Circuit breakers**: daily $9, module $90, lifetime $60 pause / $150 hard stop
    - **Persistent state**: daily/lifetime losses survive process restarts via SQLite WAL
    - **Kill switch**: .system\_pause.lock file checked on every signal evaluation
    - **Min notional rejection**: never expands risk to meet exchange minimums
    - **Liquidation safety**: stop distance validated against estimated liquidation distance
    - **Gap-through-stop modeling**: conservative fill at worst of (bar.open, stop\_price)
    - **Durable intent journal**: orders written to SQLite BEFORE network dispatch
    - **Reconciler**: fail-closed stop verification (17 checks), incremental VWAP fill tracking, NaN/Inf rejection
    - **News regime guard**: macro embargo ±15min around CPI/FOMC/NFP, stablecoin depeg detection, geopolitical flight-to-gold filter

---

**6. AI/ML COMPONENTS**

**There are NO AI/ML components in the codebase.** This was an explicit architectural decision:

From IMPLEMENTATION\_PLAN.md:

**FinBERT / Local LLM**: Very High effort (10+ days), **Negative ROI** — High CPU/RAM drag, no proven edge → **REJECTED**

The news engine uses **regex-based keyword classification** (not ML):

    - CRASH\_KEYWORDS: hack, exploit, drained, delist, bankrupt, etc.
    - CATALYST\_KEYWORDS: listing, ETF approval, buyback, burn, mainnet, etc.
    - GEOPOL\_KEYWORDS: missile, air strike, war declared, etc.

Headline classification produces a simple integer score (-3 to +3) and event\_type string. No sentiment models, no embeddings, no LLMs.

---

**7. CONFIGURATION SYSTEM**

The system is configured via Python dataclasses and constants in src/config.py:

**What's parameterizable:**

    - Risk profiles (5 predefined: LIVE\_MICRO\_CEX, SHADOW, BASE, AGGRESSIVE, EXTREME)
    - Fee rates (taker/maker/slippage)
    - Capital allocation ($180 CEX / $60 Solana / $60 reserve)
    - Loss limits (daily, module, lifetime)
    - Position constraints (max positions, max leverage, pyramiding)
    - Instrument specs (qty\_step, min\_qty, price\_step, min\_notional per asset)
    - Asset universe (primary + holdout)
    - Strategy parameters (in module constructors — BB period, KC multiplier, squeeze bars, vol threshold, etc.)

**What's NOT configurable (hardcoded):**

    - Strategy selection and combination (hardcoded in backtester)
    - Entry/exit logic thresholds (scattered across backtester.py inline code)
    - No YAML/JSON/TOML config file — everything is Python constants
    - No dynamic parameter loading or hot-reload capability
    - Guard mode selection (passed as constructor arg to BacktestEngine)

---

**8. KEY DOCUMENTATION SUMMARY**

**PROJECT\_STATE.md**: 24 modules (M01-M24) all marked PASS except M21 (Solana=STOP) and M22 (Funding=READ\_ONLY). Last audit: R3.1 with 77/77 tests verified.

**IMPLEMENTATION\_PLAN.md**: 4-week plan:

    - Week 1 ✅: Audit, True 1R economics, core architecture
    - Week 2 🔄: Historical OOS validation (in progress — need multi-year walk-forward, bootstrap CIs)
    - Week 3 ❌: Forward shadow engine + WebSocket ingestion
    - Week 4 ❌: Reliability invariants + micro-live readiness

**Gate Status**: G0=PASS, G1=PASS, G2=IN PROGRESS (CI spans zero with N=20), G3=PENDING, G4=PARTIAL PASS (unit/mock only), G5=LOCKED, G6=LOCKED

**EXPERIMENTS.md**: 7 experiments registered. Key finding: News Guard saves +1.23 with 20 trades but 95% bootstrap CI spans zero \[-0.73\].

**DECISIONS.md**: 12 ADRs covering True 1R sizing, SQLite WAL, single slot, exit FSM, gap fill modeling, kill switch, stop verification, cumulative fill tracking, NaN rejection.

---

**9. BACKTESTING**

    - **Engine**: Event-driven, synchronized multi-asset on 5m bars
    - **Indicator pipeline**: Vectorized pre-computation, resampled to 1h/4h with strict shift(1) for zero lookahead
    - **Execution model**: Realistic VIP0 fees + adverse slippage + queue penalty + funding rates
    - **Evaluation**: Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014), rolling 105-day cohorts, bootstrap CIs
    - **Stress regimes**: SVB Collapse (Mar 2023), Geopolitics (Apr-May 2024), US Elections (Nov 2024), Bybit Hack (Feb 2025)
    - **Baselines**: Side-by-side comparison with Donchian-20 and EMA 50/200
    - **Limitations**: No multi-year walk-forward completed yet (N=20 trades insufficient for statistical significance)

---

**10. NEWS/SENTIMENT ANALYSIS**

**Two-layer system:**

    - **NewsRegimeGuard** (defensive layer):
        - Macro embargo ±15min around CPI/FOMC/NFP
        - Stablecoin depeg detection (|USDC - 1.0| > 30bps)
        - Critical shock detection (hack/exploit/delist → block longs 24h)
        - Geopolitical flight-to-gold (BTC ≤-2.5% + PAXG ≥+0.6%)
        - Positive catalyst boost (+25% quality score for 6h)
    - **NewDragonNewsCore (NENE v2.0)** (active trading layer):
        - 45-minute semantic deduplication
        - Market confirmation gate (volume ≥4x median + price ≥|0.8%|)
        - Joint position management matrix (emergency eject, short squeeze escape, super-runner boost, tight shield)
        - Sector sympathy map (ETH→LDO/ARB/OP, SOL→JUP/PYTH, etc.)
        - 3 active trading scenarios (N1 pullback, N2 sympathy, N3 crash breakdown)
        - Slot pre-emption for small accounts

**Data sources**: Telegram JSON archives (BWE News, Bybit Announcements) — keyword regex classification, NOT ML sentiment.

---

**11. GAPS FOR "UNIVERSAL CONFIGURABLE TRADING SYSTEM"**

| **Gap** | **Current State** | **What's Needed** |
| --- | --- | --- |
| **No external config files** | All params hardcoded in Python | YAML/JSON config with hot-reload |
| **No live data streaming** | Batch Parquet only | WebSocket listener for orderbook, trades, OI |
| **No strategy registry/plugin system** | Strategies hardcoded inline in backtester.py (917 lines) | Strategy plugin interface with dynamic loading |
| **No live exchange adapter** | Simulated fills only | Bybit V5 authenticated REST + WebSocket adapter |
| **No parameter optimization** | Manual parameter selection | Grid search / Bayesian optimization framework |
| **No multi-exchange support** | Bybit-only specs | Exchange adapter abstraction layer |
| **No ML/AI integration** | Regex-only news classification | Optional ML sentiment, feature importance analysis |
| **No dashboard/UI** | Console logging only | Real-time monitoring dashboard |
| **No forward shadow engine** | Planned for Week 3 | Paper trading with live data comparison |
| **Statistical significance** | N=20 trades, CI spans zero | Multi-year walk-forward needed (N≥100) |
| **Strategy combination** | Fixed ensemble in backtester | Configurable strategy weights/allocations |
| **No alerting beyond console** | ObservabilityEngine logs to file | Telegram/Discord/email alerts |

The codebase is **architecturally sound and well-audited** (4 rounds of red-team + 77 passing tests) but is essentially a **single-use backtesting harness** rather than a configurable trading platform. The strategies, parameters, and pipeline are tightly coupled in the backtester. To become a "universal configurable trading system," it needs decoupling of strategy logic from execution, an external configuration layer, live data streaming, and a strategy plugin architecture.

 -----------------------------------------------------------




