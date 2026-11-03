"""
Multi-Coin Universe Scanner and Momentum Ranking Module v2.1.
Enhanced with:
1. Strict audited breakout and TMS formulas
2. Optional ADX filter
3. BTC Delta Trend Filter (altcoin longs require BTC bullish; shorts require BTC bearish)
4. Re-entry Cooldown (blocks re-entry for N bars after a position closes)
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
import numpy as np


@dataclass
class CoinCandidate:
    symbol: str
    direction: int  # +1 Long, -1 Short
    close_price: Decimal
    breakout_boundary: Decimal
    atr: Decimal
    trend_momentum_score: float
    volume_ratio: float
    normalized_breakout_dist: float

    # Backward compatibility aliases
    @property
    def breakout_price(self) -> Decimal:
        return self.close_price

    @property
    def tms_score(self) -> Decimal:
        return Decimal(str(self.trend_momentum_score))


class MultiCoinScanner:
    """
    Scans a multi-asset universe and selects top opportunities by Trend Momentum Score.
    """

    def __init__(
        self,
        max_concurrent_positions: int = 2,
        min_volume_ratio: float = 1.2,
        min_atr_distance: float = 0.0,
        min_adx: float = 0.0,
        enable_btc_delta_filter: bool = True,
    ):
        self.max_concurrent_positions = max_concurrent_positions
        self.min_volume_ratio = min_volume_ratio
        self.min_atr_distance = min_atr_distance
        self.min_adx = min_adx
        self.enable_btc_delta_filter = enable_btc_delta_filter

    def evaluate_symbol_bar(
        self,
        symbol: str,
        close: float,
        upper_channel: float,
        lower_channel: float,
        ema_fast: float,
        ema_slow: float,
        atr: float,
        volume: float,
        volume_sma: float,
        adx: float = float("nan"),
    ) -> Optional[CoinCandidate]:
        """
        Evaluates a single instrument's bar for breakout signals and calculates TMS.
        """
        if not all(
            np.isfinite(v)
            for v in (close, upper_channel, lower_channel, ema_fast, ema_slow, atr, volume, volume_sma)
        ):
            return None
        if min(close, atr, volume_sma) <= 0 or volume < 0:
            return None
        if self.min_adx and (not np.isfinite(adx) or adx < self.min_adx):
            return None

        vol_ratio = volume / volume_sma
        if vol_ratio < self.min_volume_ratio:
            return None

        # Long breakout: close > upper and EMA50 > EMA200 (or fast > slow)
        if close > upper_channel and ema_fast > ema_slow:
            breakout_dist = (close - upper_channel) / atr
            if breakout_dist < self.min_atr_distance:
                return None

            tms = (abs(close - ema_fast) / atr) * vol_ratio
            return CoinCandidate(
                symbol=symbol,
                direction=1,
                close_price=Decimal(str(close)),
                breakout_boundary=Decimal(str(upper_channel)),
                atr=Decimal(str(atr)),
                trend_momentum_score=float(tms),
                volume_ratio=float(vol_ratio),
                normalized_breakout_dist=float(breakout_dist),
            )

        # Short breakdown: close < lower and EMA50 < EMA200
        if close < lower_channel and ema_fast < ema_slow:
            breakout_dist = (lower_channel - close) / atr
            if breakout_dist < self.min_atr_distance:
                return None

            tms = (abs(ema_fast - close) / atr) * vol_ratio
            return CoinCandidate(
                symbol=symbol,
                direction=-1,
                close_price=Decimal(str(close)),
                breakout_boundary=Decimal(str(lower_channel)),
                atr=Decimal(str(atr)),
                trend_momentum_score=float(tms),
                volume_ratio=float(vol_ratio),
                normalized_breakout_dist=float(breakout_dist),
            )

        return None

    # Alias for legacy calls
    evaluate_symbol = evaluate_symbol_bar

    def rank_candidates(
        self,
        candidates: List[CoinCandidate],
        currently_held_symbols: List[str],
        current_long_count: int,
        current_short_count: int,
        btc_bullish: bool = True,
        btc_bearish: bool = True,
        cooldown_tracker: Optional[Dict[Tuple[str, int], int]] = None,
        current_bar_idx: int = 0,
    ) -> List[CoinCandidate]:
        """
        Filters out held symbols, enforces portfolio direction limits,
        applies BTC Delta Filter and Cooldown, then sorts by TMS descending.
        """
        valid_candidates = []

        for cand in candidates:
            # Skip if already in position
            if cand.symbol in currently_held_symbols:
                continue

            # Check portfolio directional limit
            if cand.direction == 1 and current_long_count >= self.max_concurrent_positions:
                continue
            if cand.direction == -1 and current_short_count >= self.max_concurrent_positions:
                continue

            # Cooldown check
            if cooldown_tracker and (cand.symbol, cand.direction) in cooldown_tracker:
                if current_bar_idx < cooldown_tracker[(cand.symbol, cand.direction)]:
                    continue

            # BTC Delta Filter: altcoins require BTC trend confirmation
            if self.enable_btc_delta_filter and cand.symbol != "BTCUSDT":
                if cand.direction == 1 and not btc_bullish:
                    continue
                if cand.direction == -1 and not btc_bearish:
                    continue

            valid_candidates.append(cand)

        # Sort by Trend Momentum Score descending
        valid_candidates.sort(key=lambda c: c.trend_momentum_score, reverse=True)
        return valid_candidates
