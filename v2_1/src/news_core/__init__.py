"""
News Core Package.
Provides news classification, market confirmation, position shielding, and offensive execution.
"""

try:
    from .models import (
        Side,
        EventType,
        ActionType,
        PositionView,
        PositionAction,
        OrderIntent,
        MarketCandle,
        RegimeStatus,
    )
    from .news_config import NewsConfig
    from .news_core_final import NewsCore
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
        RegimeStatus,
    )
    from news_config import NewsConfig
    from news_core_final import NewsCore
    from classifier import NewsClassifier
    from market_confirmation import MarketConfirmationEngine
    from position_matrix import PositionMatrix
    from offensive_engine import OffensiveEngine
    from regime_guard import RegimeGuard

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
    "NewsClassifier",
    "MarketConfirmationEngine",
    "PositionMatrix",
    "OffensiveEngine",
    "RegimeGuard",
]
