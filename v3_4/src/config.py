"""
Configuration parameters for Multi-Alpha Growth Engine v3.4 ZF-8 Quant.
Features:
- Timeframe: 1h ("60" in Bybit linear API)
- Donchian Window: 120 bars (120 hours = 5 days)
- Bar Interval: 3,600,000 ms (1 hour)
- Dedicated Port: 8087
- Dynamic Universe Screener (Top 25 Bybit Linear Perpetuals)
- News Core v1.2 Defensive Shield (Pre-Macro 30% Trim + Asymmetric Lockout [-45m : +30m])
- 8-Speed Intelligent ZF Quant Transmission:
  * Gear 1 (Limp Mode / Crawl): Risk 1.0%, Target R:R 1:2.8 - 1:3.6, Pyramiding OFF
  * Gear 2 (Base City): Risk 1.3%, Target R:R 1:3.0 - 1:4.0, Pyramiding +20%
  * Gear 3 (Steady Cruise): Risk 1.6%, Target R:R 1:3.3 - 1:4.4, Pyramiding +35%
  * Gear 4 (Drive / Mid): Risk 2.0%, Target R:R 1:3.6 - 1:4.8, Pyramiding +50%
  * Gear 5 (Fast Lane): Risk 2.4%, Target R:R 1:4.0 - 1:5.2, Pyramiding +65%
  * Gear 6 (Sport): Risk 2.8%, Target R:R 1:4.4 - 1:5.6, Pyramiding +75%
  * Gear 7 (Sport+): Risk 3.2%, Target R:R 1:4.8 - 1:6.0, Pyramiding +85%
  * Gear 8 (Launch / Overdrive): Risk 3.6%, Target R:R 1:5.2 - 1:6.5, Pyramiding +100%
- Intelligent Kickdown: Big runners (+3.2R to +5.0R) upshift +2 gears, mega runners (+5.0R+) upshift +3 gears.
- Rapid ABS Downshift: Full stop loss downshifts -2 gears immediately to protect accumulated profits.
- Scratch Neutral: Breakeven / scratches preserve current gear.
- Circuit Breaker: Drops to Gear 1 (1.0% risk) if DD >= 8.0%.
"""

from decimal import Decimal
from pathlib import Path

# Paths
BASE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = BASE_DIR.parent
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

PERSISTENT_STATE_FILE = REPORTS_DIR / "BOT_STATE_PERSISTENT_V34.json"
LIVE_STATUS_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V34.json"
LIVE_MD_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V34.md"

# Server Port (Dedicated for v3.4 ZF-8 Quant)
PORT = 8087
VERSION_NAME = "v3.4 ZF-8 Quant (8-Speed Transmission)"

# Base Universe Fallback (Top Liquid Assets)
DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "DOGEUSDT", "AVAXUSDT",
    "NEARUSDT", "LINKUSDT", "APTUSDT", "ADAUSDT", "XRPUSDT", "BNBUSDT",
    "TIAUSDT", "INJUSDT", "ARBUSDT", "OPUSDT", "1000PEPEUSDT", "RENDERUSDT"
]
SYMBOLS = DEFAULT_SYMBOLS

# Dynamic Universe Screener Configuration
ENABLE_DYNAMIC_UNIVERSE = True
UNIVERSE_TOP_N = 25                    # Keep active top 25 high-momentum coins
SCREENER_INTERVAL_HOURS = 1            # Refresh screener every 1h aligned with 1h bars
MIN_24H_TURNOVER = 25_000_000.0        # $25M min 24h turnover
MIN_OPEN_INTEREST = 5_000_000.0        # $5M min open interest
MAX_FUNDING_RATE_ABS = 0.0005          # |Funding| <= 0.05% per 8h
MIN_NORM_ATR = 0.015                   # Min Normalized ATR (1.5%)

# Candle Timeframe & Bar Interval (1-Hour Resolution)
CANDLE_INTERVAL = "60"                 # 1-hour candles ("60" in Bybit API)
BAR_INTERVAL_MS = 3600 * 1000          # 3,600,000 ms
DONCHIAN_WINDOW = 120                  # 120 bars (120 hours / 5 days Donchian channel)

# Core Capital & Risk Baseline
INITIAL_CAPITAL = Decimal("300.0")
MAX_LEVERAGE_CAP = Decimal("4.0")      # Hard 4.0x position leverage cap
PORTFOLIO_LEVERAGE = Decimal("4.0")    # Hard 4.0x aggregate portfolio cap
MAX_CONCURRENT_POSITIONS = 4           # Maximum 4 concurrent active positions
DRAWDOWN_LIMIT_PCT = Decimal("30.0")   # Automated trading halt at 30% DD
CIRCUIT_BREAKER_DD_PCT = Decimal("8.0")# Drops to Gear 1 (Limp Mode) if DD >= 8.0%
CIRCUIT_BREAKER_RECOVERY_DD_PCT = Decimal("4.0") # Recovers when DD <= 4.0%
ATR_STOP_MULT = Decimal("2.5")         # 2.5x ATR initial stop-loss

# Dynamic Cascade Take-Profit Enable
ENABLE_CASCADE_TP = True

# Trend Regime Filter
MIN_ADX_TREND = 18.0                   # ADX >= 18.0 catches trend ignition and blocks chop
REENTRY_COOLDOWN_BARS = 3              # 3-bar (3-hour) cooldown after closing a position
ENABLE_BTC_DELTA_FILTER = True         # Require BTC bullish trend for altcoin longs

# Fast-Scratch Defense
ENABLE_FAST_SCRATCH_EXIT = True        # Early cut if adverse excursion >= 0.5 ATR by bar 3
FAST_SCRATCH_BARS = 3
FAST_SCRATCH_MFE_R = Decimal("0.3")

# Bybit Non-VIP Execution Friction
BASE_TAKER_FEE = Decimal("0.0009")     # 0.09% (with MNT 10% fee discount)
BASE_SLIPPAGE = Decimal("0.0002")      # 0.02% conservative execution slippage
