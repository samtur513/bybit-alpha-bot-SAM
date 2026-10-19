"""
Multi-Coin Universe Scanner and Momentum Ranking Module.
Strict adherence to SPEC_V2.md Section 3.1.
Selects and ranks highest-momentum breakout candidates while enforcing portfolio correlation limits.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional
import numpy as np
import pandas as pd


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


class MultiCoinScanner:
    """
    Scans a multi-asset universe and selects top opportunities by Trend Momentum Score.
    """

    def __init__(
        self,
        max_concurrent_positions: int = 3,
        min_volume_ratio: float = 1.15,
        min_atr_distance: float = 0.20,
    ):
        self.max_concurrent_positions = max_concurrent_positions
        self.min_volume_ratio = min_volume_ratio
        self.min_atr_distance = min_atr_distance

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
    ) -> Optional[CoinCandidate]:
        """
        Evaluates a single instrument's bar for breakout signals and calculates TMS.
        """
        if (
            np.isnan(upper_channel)
            or np.isnan(lower_channel)
            or atr <= 0
            or volume_sma <= 0
        ):
            return None

        vol_ratio = volume / volume_sma
        if vol_ratio < self.min_volume_ratio:
            return None

        # Long breakout: close > upper and EMA50 > EMA200
        if close > upper_channel and ema_fast > ema_slow:
            breakout_dist = (close - upper_channel) / atr
            if breakout_dist < self.min_atr_distance:
                return None

            tms = ((close - ema_fast) / atr) * vol_ratio
            return CoinCandidate(
                symbol=symbol,
                direction=1,
                close_price=Decimal(str(round(close, 6))),
                breakout_boundary=Decimal(str(round(upper_channel, 6))),
                atr=Decimal(str(round(atr, 6))),
                trend_momentum_score=float(tms),
                volume_ratio=float(vol_ratio),
                normalized_breakout_dist=float(breakout_dist),
            )

        # Short breakdown: close < lower and EMA50 < EMA200
        if close < lower_channel and ema_fast < ema_slow:
            breakout_dist = (lower_channel - close) / atr
            if breakout_dist < self.min_atr_distance:
                return None

            tms = ((ema_fast - close) / atr) * vol_ratio
            return CoinCandidate(
                symbol=symbol,
                direction=-1,
                close_price=Decimal(str(round(close, 6))),
                breakout_boundary=Decimal(str(round(lower_channel, 6))),
                atr=Decimal(str(round(atr, 6))),
                trend_momentum_score=float(tms),
                volume_ratio=float(vol_ratio),
                normalized_breakout_dist=float(breakout_dist),
            )

        return None

    def rank_candidates(
        self,
        candidates: List[CoinCandidate],
        currently_held_symbols: List[str],
        current_long_count: int,
        current_short_count: int,
    ) -> List[CoinCandidate]:
        """
        Filters out already-held symbols, enforces portfolio direction limits,
        and sorts remaining candidates by Trend Momentum Score (highest first).
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

            valid_candidates.append(cand)

        # Sort by Trend Momentum Score descending
        valid_candidates.sort(key=lambda c: c.trend_momentum_score, reverse=True)
        return valid_candidates
