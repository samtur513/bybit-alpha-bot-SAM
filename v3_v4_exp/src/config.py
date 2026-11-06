"""
Configuration for v3-v4-exp: NewDragon Trend Lab (Port 8082).
Combines Bybit 1h/4h Donchian Breakout with NewDragon v4 Phase 1:
- UniversalRiskBridge True 1R Position Sizer with Portfolio Heat Guard & Correlation Protection
- AdaptiveExitBridge with Dynamic Stage 2 Sizing & MFE Breakeven Protection
- News Core v1.2 Defensive Shield
"""

from decimal import Decimal
import os
from pathlib import Path

VERSION = "4.0.0-exp1"
VERSION_NAME = "v3-v4 NewDragon Trend Lab"
PORT = int(os.environ.get("PORT_V3_V4_EXP", 8082))
TIMEFRAME = "1h"
CANDLE_INTERVAL = "60"
BAR_INTERVAL_MS = 3600 * 1000

INITIAL_CAPITAL = Decimal(str(os.environ.get("EQUITY_USDT", "300.0")))
TAKER_FEE = Decimal("0.00055")
MAKER_FEE = Decimal("0.00020")
SLIPPAGE = Decimal("0.0005")
MAX_LEVERAGE = 10.0
MAX_PORTFOLIO_HEAT_PCT = 0.045
MAX_CONCURRENT_POSITIONS = 2
MAX_CORRELATED_POSITIONS = 2

# Strategy Filters
DONCHIAN_WINDOW = 20
MIN_ADX = 18.0
STRONG_TREND_ADX = 25.0
MIN_TURNOVER_24H_USDT = 5_000_000.0
MAX_ACTIVE_SYMBOLS = 16
SCREENER_ROTATION_INTERVAL_HOURS = 4.0

# 4-Stage Adaptive Exit FSM Thresholds
STAGE1_R = 0.85
STAGE1_LOCK_R = -0.35
STAGE1_MFE_TRIGGER_R = 1.00
STAGE1_MFE_PULLBACK_RATIO = 0.35
STAGE1_MFE_BE_BUFFER_PCT = 0.0020

STAGE2_R = 1.40
STAGE2_STRONG_TREND_CLOSE_PCT = 0.40  # 60% runner in strong trends
STAGE2_CHOP_CLOSE_PCT = 0.70          # Lock 70% in choppy markets
STAGE2_DEFAULT_CLOSE_PCT = 0.60
STAGE2_LOCK_R = 0.35

STAGE3_R = 2.00
CHANDELIER_MULT = 2.5
CHANDELIER_COMPRESSED_MULT = 1.2

# Persistence paths
BASE_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = BASE_DIR / "reports"
PERSISTENT_STATE_FILE = BASE_DIR / "paper_state_v3_v4.json"
LIVE_STATUS_FILE = BASE_DIR / "live_status_v3_v4.json"
LIVE_MD_FILE = BASE_DIR / "live_status_v3_v4.md"

REPORTS_DIR.mkdir(parents=True, exist_ok=True)
