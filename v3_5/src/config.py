"""
Configuration parameters for Multi-Alpha Market-Neutral Engine v3.5 Alpha Pairs.
Features:
- Timeframe: 1h ("60" in Bybit linear API)
- Cross-Sectional Momentum (CAR-CSMOM): 168 hours (7 days lookback)
- Dedicated Port: 8088
- Market-Neutral Beta ~ 0.0 (Long Top 2 Leaders / Short Bottom 2 Laggards)
- Hysteresis Rank Buffer:
  * Entry: Top 2 Long / Bottom 2 Short (RS > +2.5% / RS < -2.5%)
  * Exit: Drops below Top 5 / Rises above Bottom 5, or RS crosses 0
- Execution Model: 3-Minute Adaptive Limit-Chase (70% Maker / 30% Taker fallback)
- Trailing Stop: 2.0x ATR after +1.5R, Partial TP at +4.0R (50% trim)
- News Core v1.2 Defensive Shield:
  * Scheduled Macro Lockout [-45m : +30m] with 30% profit trim at >= 1.0 ATR
  * Emergency Eject on critical exploits/hacks
"""

from decimal import Decimal
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

PERSISTENT_STATE_FILE = REPORTS_DIR / "BOT_STATE_PERSISTENT_V35.json"
LIVE_STATUS_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V35.json"
LIVE_MD_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V35.md"

# Server Port (Dedicated for v3.5 Alpha Pairs)
PORT = 8088
VERSION_NAME = "v3.5 Alpha Pairs (Market-Neutral L/S)"

# Trading Universe (18 High-Liquidity Perps + BTC Benchmark)
BENCHMARK_SYMBOL = "BTCUSDT"
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "DOGEUSDT", "AVAXUSDT",
    "NEARUSDT", "LINKUSDT", "APTUSDT", "ADAUSDT", "XRPUSDT", "BNBUSDT",
    "TIAUSDT", "INJUSDT", "ARBUSDT", "OPUSDT", "1000PEPEUSDT", "RENDERUSDT"
]

# Candle Timeframe & Bar Interval (1-Hour Resolution)
CANDLE_INTERVAL = "60"                 # 1-hour candles ("60" in Bybit API)
BAR_INTERVAL_MS = 3600 * 1000          # 3,600,000 ms

# Quantitative Strategy Parameters
MOMENTUM_WINDOW_HOURS = 168            # 168-hour (7 days) relative momentum vs BTC
REBALANCE_INTERVAL_HOURS = 12          # Ranking check every 12 hours
ENTRY_TOP_K = 2                        # Max 2 Longs
ENTRY_BOTTOM_K = 2                     # Max 2 Shorts
MIN_REL_STRENGTH_LONG = Decimal("0.025")   # Long candidate must outperform BTC by >= 2.5%
MIN_REL_STRENGTH_SHORT = Decimal("-0.025") # Short candidate must underperform BTC by <= -2.5%
MIN_KER_THRESHOLD = Decimal("0.0")         # Softly weighted in composite rank score: rs * (0.6 + 0.4 * ker)

# Hysteresis Rank Retention
HYSTERESIS_TOP_RANK = 5                # Keep existing Long while in Top 5 and RS > 0
HYSTERESIS_BOTTOM_RANK = 5             # Keep existing Short while in Bottom 5 and RS < 0

# Minimum Alpha Spread & Rebalance Friction Filters
MIN_PAIR_NET_ALPHA_SPREAD = Decimal("0.050")       # 5.0% min spread between candidate Long RS and Short RS (RS_L - RS_S >= 5.0%)
MIN_REBALANCE_SPREAD_ADVANTAGE = Decimal("0.020") # 2.0% alpha score advantage required for candidate to replace held asset
HYSTERESIS_EXIT_RS_BUFFER = Decimal("0.015")      # 1.5% negative deadband buffer before hysteresis exit (RS < -1.5% for Long)
MIN_HOLD_BARS_REBALANCE = 6                       # Minimum 6 hours hold before allowing hysteresis exit
PROTECT_PROFITABLE_RUNNERS = True                 # Prevent hysteresis rotation if position is profitable (unrealized > 0)

# Core Capital & Risk Baseline
INITIAL_CAPITAL = Decimal("300.0")
BASE_RISK_PER_LEG_PCT = Decimal("1.5") # 1.5% capital risk per individual leg
MAX_LEVERAGE_PER_LEG = Decimal("1.5")  # Max 1.5x leverage per leg
MAX_PORTFOLIO_LEVERAGE = Decimal("4.0")# Total portfolio leverage cap (sum of legs <= 4.0x)
DRAWDOWN_LIMIT_PCT = Decimal("30.0")   # Trading halt threshold

# Stop Loss & Take Profit Parameters
ATR_PERIOD = 24
STOP_LOSS_ATR_MULT = Decimal("2.5")    # 2.5x ATR initial stop
TRAILING_ACTIVATION_R = Decimal("1.5") # Activate trailing stop after +1.5R
TRAILING_DISTANCE_ATR = Decimal("2.0") # Trail behind highest/lowest price by 2.0x ATR
PARTIAL_TP_R = Decimal("4.0")          # Partial TP at +4.0R (close 50%)
PARTIAL_TP_RATIO = Decimal("0.50")     # 50% partial exit ratio

# Bybit Non-VIP Execution Friction (Adaptive Limit Chase Model)
# 70% Maker (0.02%), 30% Taker (0.055%)
BLENDED_FEE_RATE = Decimal("0.000305") # 0.0305%
BASE_SLIPPAGE = Decimal("0.0002")      # 0.02% conservative slippage
