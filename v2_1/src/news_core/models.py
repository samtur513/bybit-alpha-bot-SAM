"""
News Core Data Models and Types.
Defines types for event classification, position views, position actions,
order intents, regime guard status, and market candles.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any


class Side(str, Enum):
    """Trading direction."""
    LONG = "LONG"
    SHORT = "SHORT"

    @classmethod
    def from_str(cls, val: str) -> "Side":
        val_upper = str(val).upper().strip()
        if "BUY" in val_upper or "LONG" in val_upper:
            return cls.LONG
        return cls.SHORT


class EventType(str, Enum):
    """News event classification types."""
    CRASH_SHOCK = "CRASH_SHOCK"          # Score: -3 (hack, exploit, delist, bankruptcy, depeg, SEC sues)
    MAJOR_CATALYST = "MAJOR_CATALYST"    # Score: +3 (Binance/Upbit listing, ETF approval, buyback, mainnet)
    GEOPOLITICAL_SHOCK = "GEOPOLITICAL_SHOCK"  # Score: -2 (missile, air strike, war declared)
    NEUTRAL = "NEUTRAL"                  # Score: 0 (everything else)


class ActionType(str, Enum):
    """Actions emitted to the main bot."""
    CLOSE_MARKET = "CLOSE_MARKET"        # Emergency Eject or Squeeze Escape (100% close)
    MODIFY_STOP_TP = "MODIFY_STOP_TP"    # Super-Runner breakeven tightening
    PLACE_ORDER = "PLACE_ORDER"          # Offensive entry (N1 pullback or N3 crash short)
    NO_ACTION = "NO_ACTION"


@dataclass
class PositionView:
    """Snapshot of an open position in the main bot."""
    symbol: str
    side: Side
    size: float = 0.0
    entry_price: float = 0.0
    current_price: float = 0.0
    stop_loss: float = 0.0
    take_profit: float = 0.0
    unrealized_pnl_pct: float = 0.0
    unrealized_pnl_r: float = 0.0
    # v1.1 compatibility aliases
    qty: Optional[float] = None
    current_stop: Optional[float] = None
    unrealized_r: Optional[float] = None
    source_module: str = "MAIN"

    def __post_init__(self):
        # Synchronize aliases
        if self.qty is not None and self.size == 0.0:
            self.size = self.qty
        elif self.qty is None:
            self.qty = self.size

        if self.current_stop is not None and self.stop_loss == 0.0:
            self.stop_loss = self.current_stop
        elif self.current_stop is None:
            self.current_stop = self.stop_loss

        if self.unrealized_r is not None and self.unrealized_pnl_r == 0.0:
            self.unrealized_pnl_r = self.unrealized_r
        elif self.unrealized_r is None:
            self.unrealized_r = self.unrealized_pnl_r

    def calculate_r_multiple(self) -> float:
        """Calculate current return in R multiples based on initial risk."""
        if self.stop_loss <= 0.0:
            return 0.0
        
        if self.side == Side.LONG:
            risk = self.entry_price - self.stop_loss
            if risk <= 0:
                return 0.0
            return (self.current_price - self.entry_price) / risk
        else:
            risk = self.stop_loss - self.entry_price
            if risk <= 0:
                return 0.0
            return (self.entry_price - self.current_price) / risk


@dataclass
class PositionAction:
    """Action recommendation for an open position."""
    action_type: ActionType
    symbol: str
    side: Side
    size: float
    stop_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    reason: str = ""
    urgency: str = "HIGH"  # IMMEDIATE, HIGH, MEDIUM
    created_at: Optional[datetime] = None


# Alias for v1.1 naming compatibility
EngineAction = PositionAction


@dataclass
class OrderIntent:
    """Recommended offensive entry order."""
    symbol: str
    side: Side
    order_type: str  # "LIMIT" or "MARKET"
    limit_price: float
    stop_loss: float
    take_profit: Optional[float] = None
    size: float = 0.0
    post_only: bool = True
    strategy_tag: str = "N1_PULLBACK"  # "N1_PULLBACK", "N3_CRASH_SHORT", "N2_SYMPATHY"
    reason: str = ""
    created_at: Optional[datetime] = None


@dataclass
class MarketCandle:
    """1-minute or N-minute bar data for confirmation."""
    timestamp: int  # ms unix timestamp
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float = 0.0

    @property
    def return_pct(self) -> float:
        """Single candle return from open to close."""
        if self.open <= 0:
            return 0.0
        return (self.close - self.open) / self.open

    @property
    def range(self) -> float:
        """Candle high-low range."""
        return self.high - self.low


@dataclass
class WatchedEvent:
    """Event awaiting market confirmation in Layer 2."""
    symbol: str
    event_type: EventType
    score: int
    headline: str
    detected_at: datetime
    expires_at: datetime
    base_price: float
    confirmed: bool = False
    confirmed_at: Optional[datetime] = None


@dataclass
class RegimeStatus:
    """Status returned by RegimeGuard."""
    block_new_entries: bool
    reason: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
