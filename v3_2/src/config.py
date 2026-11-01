"""
Configuration parameters for Bybit Multi-Alpha v3.2 Dual-Regime (Port 8083).
Architecture:
- Dynamic Universe Screener across 400+ Bybit Linear Perpetuals
- Dual-Regime Architecture:
  * TREND Regime (ADX >= 20.0 and Volume > SMA20): 1h Donchian Breakout (120 bars),
    Auto-Gearbox Gear 3 (2.0% Half-Kelly), Anti-Martingale Pyramiding, Cascade TP (TP1/TP2/Runner).
  * CHOP / RANGE Regime (ADX < 20.0 or BB Width Percentile <= 40%):
    Range Mean-Reversion Maker Engine (Limit Maker orders at Bollinger/Keltner outer bands,
    TP strictly at SMA20 middle band with Maker fee 0.0324%, Stop-Loss at 1.0 ATR).
  * Seamless Regime Handoff: Immediate market exit of Range position if trend breakout surges.
- NEWS CORE DEFENSIVE SHIELD: DISABLED (Pure Dual-Regime Quant Benchmark for 1-to-1 comparison with Port 8081).
"""

from decimal import Decimal
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

PERSISTENT_STATE_FILE = REPORTS_DIR / "BOT_STATE_PERSISTENT_V32.json"
LIVE_STATUS_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V32.json"
LIVE_MD_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V32.md"

PORT = 8083

# Base Universe Fallback
DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "DOGEUSDT", "AVAXUSDT",
    "NEARUSDT", "LINKUSDT", "APTUSDT", "ADAUSDT", "XRPUSDT", "BNBUSDT",
    "TIAUSDT", "INJUSDT", "ARBUSDT", "OPUSDT", "1000PEPEUSDT", "RENDERUSDT"
]
SYMBOLS = DEFAULT_SYMBOLS

# Dynamic Universe Screener Configuration
ENABLE_DYNAMIC_UNIVERSE = True
UNIVERSE_TOP_N = 25
SCREENER_INTERVAL_HOURS = 2
MIN_24H_TURNOVER = 25_000_000.0
MIN_OPEN_INTEREST = 5_000_000.0
MAX_FUNDING_RATE_ABS = 0.0005
MIN_NORM_ATR = 0.020
MIN_ADX_TREND = 18.0
ENABLE_RVOL_FILTER = True
MIN_RVOL = 1.0
ENABLE_DELTA_OI_FILTER = False
ENABLE_ATR_EXHAUSTION_GUARD = True
MAX_DAILY_ATR_EXHAUSTION = 2.5

# Timeframe & Indicators
CANDLE_INTERVAL = "60"
BAR_INTERVAL_MS = 1 * 3600 * 1000
DONCHIAN_WINDOW = 120

# Trend Risk & Sizing (Gear 3 Champion)
INITIAL_CAPITAL = Decimal("300.0")
BASE_RISK_PCT = Decimal("0.02")
CHOPPY_RISK_PCT = Decimal("0.006")
MAX_LEVERAGE_CAP = Decimal("3.5")
PORTFOLIO_LEVERAGE = Decimal("3.5")
SOFT_DRAWDOWN_LIMIT_PCT = Decimal("15.0")
DRAWDOWN_LIMIT_PCT = Decimal("30.0")
MAX_CONCURRENT_POSITIONS = 3
COOLDOWN_BARS = 3
FAST_SCRATCH_BARS = 6

# Range Mean-Reversion Maker Submodule
RANGE_RISK_PER_TRADE_PCT = Decimal("0.01")  # 1.0% fixed fractional risk
MAX_CONCURRENT_RANGE_POSITIONS = 2          # Max 2 concurrent range positions
BASE_MAKER_FEE = Decimal("0.000324")        # 0.0324% Non-VIP Bybit + 10% MNT
BASE_TAKER_FEE = Decimal("0.0009")          # 0.09% Taker fee
BASE_SLIPPAGE = Decimal("0.0002")           # 0.02% slippage on stops
RANGE_SL_ATR_MULT = Decimal("1.0")          # 1.0 ATR stop beyond boundary
MIN_TP_DIST_PCT = Decimal("0.010")          # Minimum 1.0% distance to SMA20
MAX_TP_DIST_PCT = Decimal("0.020")          # Maximum 2.0% target clamp
RANGE_COOLDOWN_BARS = 3

# Regime Classifier Thresholds
ADX_REGIME_THRESHOLD = 20.0
BB_WIDTH_PERCENTILE_THRESHOLD = 0.40        # <= 40th percentile = compression

# News Core Defense: DISABLED for Pure Dual-Regime test
ENABLE_NEWS_DEFENSE = False
