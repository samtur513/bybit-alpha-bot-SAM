"""
Configuration and constants for Bybit research and automation system.
v1.0 - Enhanced with risk profiles, expanded parameter grid, and multi-factor filters.
"""
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import datetime

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PARQUET_DATA_DIR = DATA_DIR / "parquet"
DB_PATH = DATA_DIR / "metadata.db"
REPORTS_DIR = BASE_DIR / "reports"

for d in [DATA_DIR, RAW_DATA_DIR, PARQUET_DATA_DIR, REPORTS_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# Symbols and Category
CATEGORY = "linear"
SYMBOLS = ["BTCUSDT", "ETHUSDT"]

# Dates (UTC)
WARMUP_START_DT = datetime.datetime(2024, 8, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
DEV_START_DT = datetime.datetime(2024, 10, 12, 0, 0, 0, tzinfo=datetime.timezone.utc)
DEV_END_DT = datetime.datetime(2025, 10, 12, 0, 0, 0, tzinfo=datetime.timezone.utc)
VAL_START_DT = DEV_END_DT
VAL_END_DT = datetime.datetime(2026, 4, 12, 0, 0, 0, tzinfo=datetime.timezone.utc)
FINAL_START_DT = VAL_END_DT
FINAL_END_DT = datetime.datetime(2026, 10, 12, 0, 0, 0, tzinfo=datetime.timezone.utc)

# Millisecond timestamps
WARMUP_START_TS = int(WARMUP_START_DT.timestamp() * 1000)
DEV_START_TS = int(DEV_START_DT.timestamp() * 1000)
DEV_END_TS = int(DEV_END_DT.timestamp() * 1000)
VAL_START_TS = int(VAL_START_DT.timestamp() * 1000)
VAL_END_TS = int(VAL_END_DT.timestamp() * 1000)
FINAL_START_TS = int(FINAL_START_DT.timestamp() * 1000)
FINAL_END_TS = int(FINAL_END_DT.timestamp() * 1000)

# Entry cutoff before period end (96 hours - reduced from 120h in v1.0)
PERIOD_ENTRY_CUTOFF_MS = 96 * 3600 * 1000

# Initial capital options
CAPITALS = [Decimal("300.0"), Decimal("100.0")]

# ===================== STRATEGY v1.0 PARAMETERS =====================

@dataclass(frozen=True)
class StrategyParams:
    N: int    # Channel breakout lookback hours
    K: float  # ATR multiplier for initial stop (now float for 1.5)
    RSI_LONG_MIN: int   # Min RSI for long entry
    RSI_SHORT_MAX: int  # Max RSI for short entry

# Expanded grid: N in {48, 72, 120, 168, 240}, K in {2.0, 2.5, 3.0}, RSI in {55/45, 60/40}
PARAMETER_GRID = [
    # N=48 variants (baseline)
    StrategyParams(N=48, K=2.0, RSI_LONG_MIN=55, RSI_SHORT_MAX=45),
    StrategyParams(N=48, K=2.5, RSI_LONG_MIN=55, RSI_SHORT_MAX=45),
    # N=72 variants
    StrategyParams(N=72, K=2.0, RSI_LONG_MIN=55, RSI_SHORT_MAX=45),
    StrategyParams(N=72, K=2.5, RSI_LONG_MIN=55, RSI_SHORT_MAX=45),
    # N=120 variants (5-day channel)
    StrategyParams(N=120, K=2.5, RSI_LONG_MIN=55, RSI_SHORT_MAX=45),
    StrategyParams(N=120, K=3.0, RSI_LONG_MIN=55, RSI_SHORT_MAX=45),
    # N=168 variants (7-day channel - r/algotrading proven)
    StrategyParams(N=168, K=2.5, RSI_LONG_MIN=55, RSI_SHORT_MAX=45),
    StrategyParams(N=168, K=2.5, RSI_LONG_MIN=60, RSI_SHORT_MAX=40),
    StrategyParams(N=168, K=3.0, RSI_LONG_MIN=55, RSI_SHORT_MAX=45),
    StrategyParams(N=168, K=3.0, RSI_LONG_MIN=60, RSI_SHORT_MAX=40),
    # N=240 variants (10-day channel)
    StrategyParams(N=240, K=2.5, RSI_LONG_MIN=55, RSI_SHORT_MAX=45),
    StrategyParams(N=240, K=3.0, RSI_LONG_MIN=55, RSI_SHORT_MAX=45),
]

# Fixed Strategy Rules
EMA_FAST_PERIOD_4H = 50
EMA_SLOW_PERIOD_4H = 200
EMA_PERIOD_4H = 200  # Keep for backward compat
ATR_PERIOD_1H = 14
RSI_PERIOD_1H = 14
ADX_PERIOD_4H = 14
EXIT_CHANNEL_PERIOD_1H = 20
MAX_HOLD_HOURS = 96  # Reduced from 120h
VOLUME_SMA_PERIOD = 20

# Multi-factor filter thresholds
ADX_TREND_THRESHOLD = 25      # ADX >= 25: strong trend
ADX_TRANSITION_THRESHOLD = 15 # ADX 15-25: transition (stricter RSI)
ADX_SHORT_THRESHOLD = 25      # ADX >= 25: trend confirmation for short breakdowns
ETH_VOLUME_MULT = 1.2         # Volume >= 1.2 * SMA(20) for ETH (optimized)
ETH_ADX_MIN = 25              # ADX >= 25 for ETH (aligned with macro trend threshold)
RSI_OVERBOUGHT = 80           # RSI upper limit for long
RSI_OVERSOLD = 20             # RSI lower limit for short
VOLUME_MULT_THRESHOLD = 1.2   # Volume >= 1.2 * SMA(20)
BREAKOUT_MIN_ATR_DIST = 0.3   # Min breakout distance in ATR units
EMA_INTERLEAVE_PCT = 0.005    # EMA50/EMA200 too close = no trend (0.5%)

# Breakeven and TP thresholds (in ATR multiples)
BREAKEVEN_ATR_MULT = 999.0    # Trailing stop manages runners without premature breakeven choking
TP_ATR_MULT = 3.0             # Partial TP at 3x ATR profit
TP_CLOSE_PCT = Decimal("0.50") # Close 50% at TP (MODERATE profile)
TRAILING_ATR_MULT = 1.5       # Trailing stop at 1.5x ATR from peak

# Cooldown after loss (hours)
LOSS_COOLDOWN_HOURS = 2

# Adaptive risk: streak thresholds
LOSS_STREAK_THRESHOLD = 3     # Reduce risk after 3 consecutive losses
WIN_STREAK_RECOVERY = 2       # Recover risk after 2 consecutive wins

# ===================== RISK PROFILES =====================

@dataclass(frozen=True)
class RiskProfile:
    name: str
    base_risk_pct: Decimal
    reduced_risk_pct: Decimal
    max_notional_leverage: Decimal
    daily_loss_limit_pct: Decimal
    weekly_loss_limit_pct: Decimal
    max_peak_drawdown_pct: Decimal
    dd_risk_reduction_threshold: Decimal
    dd_risk_recovery_threshold: Decimal
    cooldown_hours: int
    tp_close_pct: Decimal
    tp_atr_mult: float
    max_positions: int

RISK_PROFILES = {
    "CONSERVATIVE": RiskProfile(
        name="CONSERVATIVE",
        base_risk_pct=Decimal("0.005"),
        reduced_risk_pct=Decimal("0.0025"),
        max_notional_leverage=Decimal("2.0"),
        daily_loss_limit_pct=Decimal("0.02"),
        weekly_loss_limit_pct=Decimal("0.05"),
        max_peak_drawdown_pct=Decimal("0.20"),
        dd_risk_reduction_threshold=Decimal("0.10"),
        dd_risk_recovery_threshold=Decimal("0.07"),
        cooldown_hours=4,
        tp_close_pct=Decimal("0.60"),
        tp_atr_mult=2.5,
        max_positions=1,
    ),
    "MODERATE": RiskProfile(
        name="MODERATE",
        base_risk_pct=Decimal("0.01"),
        reduced_risk_pct=Decimal("0.005"),
        max_notional_leverage=Decimal("3.0"),
        daily_loss_limit_pct=Decimal("0.03"),
        weekly_loss_limit_pct=Decimal("0.07"),
        max_peak_drawdown_pct=Decimal("0.25"),
        dd_risk_reduction_threshold=Decimal("0.15"),
        dd_risk_recovery_threshold=Decimal("0.10"),
        cooldown_hours=2,
        tp_close_pct=Decimal("0.50"),
        tp_atr_mult=3.0,
        max_positions=1,
    ),
    "AGGRESSIVE": RiskProfile(
        name="AGGRESSIVE",
        base_risk_pct=Decimal("0.02"),
        reduced_risk_pct=Decimal("0.01"),
        max_notional_leverage=Decimal("5.0"),
        daily_loss_limit_pct=Decimal("0.05"),
        weekly_loss_limit_pct=Decimal("0.10"),
        max_peak_drawdown_pct=Decimal("0.30"),
        dd_risk_reduction_threshold=Decimal("0.15"),
        dd_risk_recovery_threshold=Decimal("0.10"),
        cooldown_hours=1,
        tp_close_pct=Decimal("0.40"),
        tp_atr_mult=3.5,
        max_positions=2,
    ),
}

# Default profile
DEFAULT_RISK_PROFILE = "MODERATE"

# Keep backward-compat constants (use MODERATE defaults)
BASE_RISK_PCT = Decimal("0.01")
REDUCED_RISK_PCT = Decimal("0.005")
DD_RISK_REDUCTION_THRESHOLD = Decimal("0.15")
DD_RISK_RECOVERY_THRESHOLD = Decimal("0.10")
MAX_NOTIONAL_LEVERAGE = Decimal("3.0")
DAILY_LOSS_LIMIT_PCT = Decimal("0.03")
WEEKLY_LOSS_LIMIT_PCT = Decimal("0.07")
MAX_PEAK_DRAWDOWN_PCT = Decimal("0.25")

# Execution Costs
BASE_TAKER_FEE = Decimal("0.0006")
BASE_SLIPPAGE = Decimal("0.0002")
STRESS_TAKER_FEE = Decimal("0.0012")
STRESS_SLIPPAGE = Decimal("0.0010")

# Infrastructure cost scenarios
INFRA_COST_MONTHLY = [Decimal("0.0"), Decimal("5.0"), Decimal("10.0")]

# Instrument rules snapshot
INSTRUMENT_RULES = {
    "BTCUSDT": {
        "minOrderQty": Decimal("0.001"),
        "qtyStep": Decimal("0.001"),
        "minNotionalValue": Decimal("5.0"),
        "tickSize": Decimal("0.10"),
        "maintenanceMarginRate": Decimal("0.005"),
    },
    "ETHUSDT": {
        "minOrderQty": Decimal("0.01"),
        "qtyStep": Decimal("0.01"),
        "minNotionalValue": Decimal("5.0"),
        "tickSize": Decimal("0.01"),
        "maintenanceMarginRate": Decimal("0.005"),
    }
}
