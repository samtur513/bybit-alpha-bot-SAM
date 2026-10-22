"""
NewsCore: Unified news intelligence and defensive module for algorithmic trading.
Provides:
1. Defensive shields: Emergency Eject, Squeeze Escape, Super-Runner, Regime Guard.
2. Offensive entries: N1 Pullback limit order, N3 Crash Short.
3. Market confirmation engine: eliminates false alarms.
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Union, Tuple

try:
    from .models import (
        Side,
        EventType,
        ActionType,
        PositionView,
        PositionAction,
        OrderIntent,
        MarketCandle,
        WatchedEvent,
        RegimeStatus,
    )
    from .news_config import NewsConfig
    from .classifier import NewsClassifier
    from .market_confirmation import MarketConfirmationEngine
    from .position_matrix import PositionMatrix
    from .offensive_engine import OffensiveEngine
    from .regime_guard import RegimeGuard
except (ImportError, ValueError):
    from models import (
        Side,
        EventType,
        ActionType,
        PositionView,
        PositionAction,
        OrderIntent,
        MarketCandle,
        WatchedEvent,
        RegimeStatus,
    )
    from news_config import NewsConfig
    from classifier import NewsClassifier
    from market_confirmation import MarketConfirmationEngine
    from position_matrix import PositionMatrix
    from offensive_engine import OffensiveEngine
    from regime_guard import RegimeGuard


class NewsCore:
    """
    Main News Core Facade.
    Connects Layer 1 (Classifier), Layer 2 (Confirmation),
    Layer 3 (Position Matrix), Layer 4 (Offensive Engine), and Regime Guard.
    """

    def __init__(self, config: Optional[NewsConfig] = None):
        self.config = config or NewsConfig()
        self.classifier = NewsClassifier(self.config)
        self.market_confirmation = MarketConfirmationEngine(self.config)
        self.position_matrix = PositionMatrix(self.config)
        self.offensive_engine = OffensiveEngine(self.config)
        self.regime_guard = RegimeGuard(self.config)

    # ---------------------------------------------------------
    # Regime Guard: Global embargo checks
    # ---------------------------------------------------------
    def get_status(self, symbol: str, now: Optional[datetime] = None) -> Dict[str, Any]:
        """
        Check if trading entries for symbol are globally blocked.
        Usage:
            status = core.get_status("SOLUSDT", now_utc)
            if status["block_new_entries"]:
                return
        """
        status_obj = self.regime_guard.check_status(symbol, now)
        return {
            "block_new_entries": status_obj.block_new_entries,
            "reason": status_obj.reason,
            "details": status_obj.details,
        }

    # ---------------------------------------------------------
    # Layer 1: Classification
    # ---------------------------------------------------------
    def classify(
        self,
        headline: str,
        symbol: Optional[str] = None,
        timestamp: Optional[datetime] = None,
        check_cooldown: bool = True,
    ) -> Tuple[EventType, int]:
        """
        Classify news headline.
        Usage:
            event, score = core.classify(headline)
            if abs(score) >= 2:
                ...
        """
        return self.classifier.classify(
            headline,
            symbol=symbol,
            timestamp=timestamp,
            check_cooldown=check_cooldown,
        )

    def extract_symbol(self, headline: str, default: str = "BTCUSDT") -> str:
        """Extract symbol from headline text."""
        return self.classifier.extract_symbol(headline, default=default)

    # ---------------------------------------------------------
    # Layer 2: Market Confirmation
    # ---------------------------------------------------------
    def watch_for_confirmation(
        self,
        symbol: str,
        event_type: EventType,
        score: int,
        headline: str,
        timestamp: datetime,
        base_price: float
    ) -> WatchedEvent:
        """Add candidate to Layer 2 watch list waiting for market confirmation."""
        return self.market_confirmation.register_candidate(
            symbol=symbol,
            event_type=event_type,
            score=score,
            headline=headline,
            timestamp=timestamp,
            base_price=base_price,
        )

    def check_market_confirmation(
        self,
        score: int,
        candle_1m: Union[MarketCandle, Dict[str, Any]],
        median_vol_60m: float,
        base_price: Optional[float] = None
    ) -> bool:
        """
        Evaluate if a 1m candle satisfies volume (>=3x median 60m) and price return direction.
        """
        if isinstance(candle_1m, dict):
            c = MarketCandle(
                timestamp=candle_1m.get("timestamp", 0),
                open=float(candle_1m.get("open", 0.0)),
                high=float(candle_1m.get("high", 0.0)),
                low=float(candle_1m.get("low", 0.0)),
                close=float(candle_1m.get("close", 0.0)),
                volume=float(candle_1m.get("volume", 0.0)),
                turnover=float(candle_1m.get("turnover", 0.0)),
            )
        else:
            c = candle_1m

        return self.market_confirmation.check_candle_confirmation(
            score=score,
            candle=c,
            median_vol_60m=median_vol_60m,
            base_price=base_price,
        )

    def update_market_candle(
        self,
        symbol: str,
        candle_1m: Union[MarketCandle, Dict[str, Any]],
        median_vol_60m: float,
        current_time: datetime
    ) -> List[WatchedEvent]:
        """Update 1m candle for symbol and return any newly confirmed events."""
        if isinstance(candle_1m, dict):
            c = MarketCandle(
                timestamp=candle_1m.get("timestamp", 0),
                open=float(candle_1m.get("open", 0.0)),
                high=float(candle_1m.get("high", 0.0)),
                low=float(candle_1m.get("low", 0.0)),
                close=float(candle_1m.get("close", 0.0)),
                volume=float(candle_1m.get("volume", 0.0)),
                turnover=float(candle_1m.get("turnover", 0.0)),
            )
        else:
            c = candle_1m

        return self.market_confirmation.update_candle(
            symbol=symbol,
            candle=c,
            median_vol_60m=median_vol_60m,
            current_time=current_time,
        )

    # ---------------------------------------------------------
    # Layer 3: Position Management Matrix
    # ---------------------------------------------------------
    def manage_positions(
        self,
        symbol: str,
        score: int,
        event: Union[EventType, str],
        open_positions: Union[List[PositionView], Dict[str, PositionView]],
        current_price: float,
        timestamp: Optional[datetime] = None,
        ret_1m: float = 0.0,
        vol_1m: float = 0.0,
        vol_median_60m: float = 1.0,
        source_tier: int = 3,  # 1=official, 2=onchain, 3=media
    ) -> List[PositionAction]:
        """
        Evaluate defensive shields on open positions with v1.1 False-Positive Guards.
        Usage:
            actions = core.manage_positions(
                symbol="SOLUSDT",
                score=score,
                event=event,
                open_positions=open_positions,
                current_price=current_price,
                ret_1m=ret_1m,
                vol_1m=vol_1m,
                vol_median_60m=vol_median_60m,
                source_tier=3
            )
        """
        return self.position_matrix.evaluate(
            symbol=symbol,
            score=score,
            event=event,
            open_positions=open_positions,
            current_price=current_price,
            timestamp=timestamp,
            ret_1m=ret_1m,
            vol_1m=vol_1m,
            vol_median_60m=vol_median_60m,
            source_tier=source_tier,
        )

    # ---------------------------------------------------------
    # Layer 4: Offensive Order Generation
    # ---------------------------------------------------------
    def generate_n1_pullback(
        self,
        symbol: str,
        high: float,
        low: float,
        side: Union[Side, str],
        deposit: float = 1000.0,
        timestamp: Optional[datetime] = None
    ) -> Optional[OrderIntent]:
        """
        Generate N1 Pullback limit order at ~45% retracement.
        Usage:
            n1 = core.generate_n1_pullback("SOLUSDT", high, low, Side.LONG)
            if n1:
                exchange.place_limit(n1.symbol, n1.limit_price, post_only=True)
        """
        side_val = Side.from_str(side) if isinstance(side, str) else side
        return self.offensive_engine.generate_n1_pullback(
            symbol=symbol,
            high=high,
            low=low,
            side=side_val,
            deposit=deposit,
            timestamp=timestamp,
        )

    def generate_n3_crash_short(
        self,
        symbol: str,
        current_price: float,
        high_price: float,
        deposit: float = 1000.0,
        timestamp: Optional[datetime] = None
    ) -> Optional[OrderIntent]:
        """
        Generate N3 Crash Short order upon confirmed hack/exploit/delisting.
        """
        return self.offensive_engine.generate_n3_crash_short(
            symbol=symbol,
            current_price=current_price,
            impulse_high=high_price,
            deposit=deposit,
            timestamp=timestamp,
        )


# Exported items for direct import
__all__ = [
    "NewsCore",
    "NewsConfig",
    "PositionView",
    "PositionAction",
    "OrderIntent",
    "Side",
    "EventType",
    "ActionType",
    "MarketCandle",
    "RegimeStatus",
]
