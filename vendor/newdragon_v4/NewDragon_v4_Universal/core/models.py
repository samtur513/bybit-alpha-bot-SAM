"""
Core Data Models for NewDragon v4.0 Universal Architecture.
Phase 1 Foundation: Dataclasses for Portfolio, Risk, Instruments, and Execution FSM.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any
import pandas as pd


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class RegimeType(str, Enum):
    BULL = "bull"
    BEAR = "bear"
    SIDEWAYS = "sideways"
    SHOCK = "shock"


@dataclass(frozen=True)
class InstrumentSpec:
    """Bybit V5 contract specifications and trading constraints."""
    symbol: str
    qty_step: float
    tick_size: float
    min_order_qty: float
    min_notional: float = 5.0
    max_leverage: float = 10.0


@dataclass
class PortfolioState:
    """Current capital, margin, drawdown, and cross-sectional portfolio metrics."""
    total_capital: float
    available_margin: float
    current_drawdown: float
    regime: str
    asset_volatility: float
    inv_vol_sum: float
    avg_correlation: float = 0.65
    cvar_95_pct: float = 0.05
    expected_mu: float = 0.015
    expected_var: float = 0.025
    current_portfolio_heat: float = 0.0       # Sum of open position risk / capital
    max_portfolio_heat: float = 0.045         # Max 4.5% total open risk
    active_correlated_positions: int = 0      # Open positions in same direction with BTC corr > 0.7
    max_correlated_positions: int = 2         # Max 2 concurrent correlated positions


@dataclass
class SignalOutput:
    """Standardized trading signal from Layer 3/4."""
    symbol: str
    side: str  # "LONG" or "SHORT"
    entry_price: float
    stop_price: float
    atr_1h: float
    scaled_forecast: float = 0.0   # Carver standardized forecast [-20.0, +20.0]
    composite_score: float = 0.0   # Composite score [-1.0, +1.0]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PositionTracker:
    """Active position state for 4-Stage Exit FSM."""
    symbol: str
    side: str  # "LONG" or "SHORT"
    entry_price: float
    current_stop: float
    initial_1r_dist: float
    remaining_qty: float
    stage: int = 0                 # 0: Initial, 1: Ratchet, 2: Partial, 3: Chandelier
    inside_bar_count: int = 0
    max_favor_price: float = 0.0


@dataclass(frozen=True)
class ExitAction:
    """Action dictated by AdaptiveExitFSM on every evaluation bar."""
    action_type: str               # "HOLD" | "UPDATE_STOP" | "PARTIAL_CLOSE_AND_STOP" | "FULL_CLOSE_SCRATCH"
    new_stop: Optional[float] = None
    close_qty_ratio: float = 0.0
    reason: str = ""
