"""
Layer 2: Market Confirmation Engine.
Ensures the bot never acts on text alone.
Requires:
1. 1m volume >= vol_multiplier * median_vol_60m (default 3x)
2. Price shift >= min_abs_return_1m (default 0.6%) in the direction of the news.
3. Confirmation within watch_window_minutes (default 15m), else event expires.
"""

from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, List

try:
    from .models import EventType, MarketCandle, WatchedEvent
    from .news_config import NewsConfig
except (ImportError, ValueError):
    from models import EventType, MarketCandle, WatchedEvent
    from news_config import NewsConfig


class MarketConfirmationEngine:
    """Manages watched candidate symbols and evaluates market confirmation on 1m bars."""

    def __init__(self, config: Optional[NewsConfig] = None):
        self.config = config or NewsConfig()
        # symbol -> list of currently watched candidates
        self._watched: Dict[str, List[WatchedEvent]] = {}

    def register_candidate(
        self,
        symbol: str,
        event_type: EventType,
        score: int,
        headline: str,
        timestamp: datetime,
        base_price: float
    ) -> WatchedEvent:
        """Register a classified news event into the market confirmation watch list."""
        expires_at = timestamp + timedelta(minutes=self.config.watch_window_minutes)
        watched = WatchedEvent(
            symbol=symbol,
            event_type=event_type,
            score=score,
            headline=headline,
            detected_at=timestamp,
            expires_at=expires_at,
            base_price=base_price,
            confirmed=False,
            confirmed_at=None,
        )
        if symbol not in self._watched:
            self._watched[symbol] = []
        self._watched[symbol].append(watched)
        return watched

    def check_candle_confirmation(
        self,
        score: int,
        candle: MarketCandle,
        median_vol_60m: float,
        base_price: Optional[float] = None
    ) -> bool:
        """
        Evaluate if a 1m candle satisfies volume and price direction conditions.
        
        Volume condition:
            candle.volume >= vol_multiplier * median_vol_60m (default 3x)
        Price condition:
            Score > 0: price return >= +min_abs_return_1m (default +0.6%)
            Score < 0: price return <= -min_abs_return_1m (default -0.6%)
        """
        # Volume check: if median_vol_60m is positive, enforce multiplier
        if median_vol_60m > 0:
            if candle.volume < self.config.vol_multiplier * median_vol_60m:
                return False

        # Price return: use base_price if available, else candle return
        if base_price and base_price > 0:
            ret = (candle.close - base_price) / base_price
        else:
            ret = candle.return_pct

        # Directional check
        if score > 0:
            return ret >= self.config.min_abs_return_1m
        elif score < 0:
            return ret <= -self.config.min_abs_return_1m
        return False

    def update_candle(
        self,
        symbol: str,
        candle: MarketCandle,
        median_vol_60m: float,
        current_time: datetime
    ) -> List[WatchedEvent]:
        """
        Process incoming 1m candle for symbol.
        Prunes expired events and returns any freshly confirmed events.
        """
        if symbol not in self._watched or not self._watched[symbol]:
            return []

        active_events = self._watched[symbol]
        remaining: List[WatchedEvent] = []
        confirmed_now: List[WatchedEvent] = []

        for ev in active_events:
            # Check expiration
            if current_time > ev.expires_at:
                # Expired without confirmation - silently dropped
                continue

            if ev.confirmed:
                # Already confirmed previously
                continue

            # Check market conditions
            is_confirmed = self.check_candle_confirmation(
                score=ev.score,
                candle=candle,
                median_vol_60m=median_vol_60m,
                base_price=ev.base_price
            )

            if is_confirmed:
                ev.confirmed = True
                ev.confirmed_at = current_time
                confirmed_now.append(ev)
            else:
                remaining.append(ev)

        self._watched[symbol] = remaining
        return confirmed_now

    def is_watching(self, symbol: str) -> bool:
        """Check if any active events are being watched for symbol."""
        return bool(self._watched.get(symbol))

    def clear(self) -> None:
        """Clear watch list."""
        self._watched.clear()
