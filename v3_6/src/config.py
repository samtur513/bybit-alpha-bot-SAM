"""
Configuration parameters for Multi-Alpha Production Engine v3.6 Omega Dual-Regime ZF-8.
Port: 8089

Architecture:
- Trend Engine: 1h Donchian Breakout (120 bars), 8-Speed Intelligent ZF Quant Transmission
  (Gears G1-G8: 1.0% to 3.6% Half-Kelly, Kickdown +2/+3 gears on runners, ABS rapid downshift,
   Limp Mode locked in G1 at 8.0% DD), Anti-Martingale Pyramiding with VWAP entry pricing,
   Dynamic Cascade TP (TP1/TP2/Runner).
- Range Engine: IRAS Range Mean-Reversion Maker in Chop (KER <= 0.15, Bandwidth >= 3.0%, ADX < 18.0),
  Limit Maker orders at Bollinger outer bands, TP at SMA20 middle band (Maker fee 0.020%), SL at 1.0 ATR.
- News Core v1.2 Defensive Shield:
  Scheduled Macro Lockout [-45m : +30m] around CPI, FOMC, NFP, 30% Profit Trim, Emergency Eject on crash shocks.
- Shared Capital Accounting: Penny-perfect invariant across $300.00 base equity.
"""

from decimal import Decimal
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

PERSISTENT_STATE_FILE = REPORTS_DIR / "BOT_STATE_PERSISTENT_V36.json"
LIVE_STATUS_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V36.json"
LIVE_MD_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V36.md"

# Server Port (Dedicated for v3.6 Omega)
PORT = 8089
VERSION_NAME = "v3.6 Omega Dual-Regime ZF-8"

# Base Universe Fallback (Top Liquid Assets)
DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "DOGEUSDT", "AVAXUSDT",
    "NEARUSDT", "LINKUSDT", "APTUSDT", "ADAUSDT", "XRPUSDT", "BNBUSDT",
    "TIAUSDT", "INJUSDT", "ARBUSDT", "OPUSDT", "1000PEPEUSDT", "RENDERUSDT"
]
SYMBOLS = DEFAULT_SYMBOLS

# Dynamic Universe Screener Configuration (Trend Engine)
ENABLE_DYNAMIC_UNIVERSE = True
UNIVERSE_TOP_N = 25
TREND_UNIVERSE_TOP_N = 15
SCREENER_INTERVAL_HOURS = 1
MIN_24H_TURNOVER = 25_000_000.0
MIN_OPEN_INTEREST = 5_000_000.0
MAX_FUNDING_RATE_ABS = 0.0005
MIN_NORM_ATR = 0.015
MIN_ADX_TREND = 18.0
ENABLE_RVOL_FILTER = True
MIN_RVOL = 1.0
ENABLE_DELTA_OI_FILTER = False
ENABLE_ATR_EXHAUSTION_GUARD = True
MAX_DAILY_ATR_EXHAUSTION = 2.5

# Dynamic Chop Universe Screener Configuration (IRAS Standard for Range Engine)
ENABLE_CHOP_SCREENER = True
CHOP_UNIVERSE_TOP_N = 10
CHOP_MIN_24H_TURNOVER = 5_000_000.0
CHOP_MIN_OPEN_INTEREST = 2_000_000.0
CHOP_MAX_KER = 0.15                     # Optimal Calibrated KER <= 0.15 (Pass 4 standard)
CHOP_MIN_BANDWIDTH_PCT = 0.030         # 3.0% minimum Bollinger Bandwidth
CHOP_MAX_ADX = 18.0                    # ADX < 18.0 = non-trending chop
CHOP_RESCAN_INTERVAL_HOURS = 1

# Candle Timeframe & Bar Interval (1-Hour Resolution)
CANDLE_INTERVAL = "60"                 # 1-hour candles ("60" in Bybit API)
BAR_INTERVAL_MS = 3600 * 1000          # 3,600,000 ms
DONCHIAN_WINDOW = 120                  # 120 bars (120 hours / 5 days Donchian channel)

# Core Capital & Risk Baseline
INITIAL_CAPITAL = Decimal("300.0")
MAX_LEVERAGE_CAP = Decimal("4.0")
PORTFOLIO_LEVERAGE = Decimal("4.0")
MAX_CONCURRENT_POSITIONS = 4           # Maximum 4 concurrent active positions
DRAWDOWN_LIMIT_PCT = Decimal("35.0")
CIRCUIT_BREAKER_DD_PCT = Decimal("8.0") # Drops to Gear 1 (Limp Mode) if DD >= 8.0%
CIRCUIT_BREAKER_RECOVERY_DD_PCT = Decimal("4.0")
ATR_STOP_MULT = Decimal("2.5")

# Dynamic Cascade Take-Profit Enable
ENABLE_CASCADE_TP = True

# Fast-Scratch Defense
ENABLE_FAST_SCRATCH_EXIT = True
FAST_SCRATCH_BARS = 3
FAST_SCRATCH_MFE_R = Decimal("0.3")
REENTRY_COOLDOWN_BARS = 3
ENABLE_BTC_DELTA_FILTER = True

# Range Engine Parameters
MIN_BANDWIDTH_CLEARANCE = Decimal("0.030") # 3.0%
RANGE_RISK_PER_TRADE_PCT = Decimal("0.010") # 1.0% risk per range trade
MAX_CONCURRENT_RANGE_POSITIONS = 2
RANGE_SL_ATR_MULT = Decimal("1.0")
MIN_TP_DIST_PCT = Decimal("0.012")
MAX_TP_DIST_PCT = Decimal("0.035")
RANGE_COOLDOWN_BARS = 3
ADX_REGIME_THRESHOLD = 18.0
BB_WIDTH_PERCENTILE_THRESHOLD = 40.0

# News Core v1.2 Defensive Shield
ENABLE_NEWS_DEFENSE = True
MACRO_LOCKOUT_BEFORE_MINUTES = 45
MACRO_LOCKOUT_AFTER_MINUTES = 30
MACRO_PROFIT_TRIM_PCT = Decimal("0.30")

# Bybit Non-VIP ("Обычный пользователь") с активной скидкой 10% в MNT
# Фьючерсы: Тейкер 0.09% (0.0009), Мейкер 0.0324% (0.000324).
BASE_TAKER_FEE = Decimal("0.0009")     # 0.090% тейкер (со скидкой 10% MNT)
BASE_MAKER_FEE = Decimal("0.000324")   # 0.0324% мейкер (со скидкой 10% MNT)
BASE_SLIPPAGE = Decimal("0.0002")      # 0.02% консервативное проскальзывание
