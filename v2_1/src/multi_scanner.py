"""
Multi-Coin Universe Scanner and Ranker v2.1.
Enhanced with:
1. BTC Delta Filter: altcoin longs require BTC bullish; altcoin shorts require BTC bearish.
2. Re-entry Cooldown: blocks re-entry for N bars after a trade closure.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import List, Dict, Tuple, Optional


@dataclass
class CoinCandidate:
    symbol: str
    direction: int  # 1 for Long, -1 for Short
    tms_score: Decimal
    atr: Decimal
    breakout_price: Decimal


class MultiCoinScanner:
    def __init__(
        self,
        max_long_positions: int = 2,
        max_short_positions: int = 2,
        volume_filter_multiplier: Decimal = Decimal("1.2"),
        enable_btc_delta_filter: bool = True,
    ):
        self.max_long_positions = max_long_positions
        self.max_short_positions = max_short_positions
        self.volume_filter_multiplier = volume_filter_multiplier
        self.enable_btc_delta_filter = enable_btc_delta_filter

    def evaluate_symbol(
        self,
        symbol: str,
        close: Decimal,
        high: Decimal,
        low: Decimal,
        upper_channel: Decimal,
        lower_channel: Decimal,
        ema_fast: Decimal,
        ema_slow: Decimal,
        atr: Decimal,
        volume: Decimal,
        volume_sma: Decimal,
    ) -> Optional[CoinCandidate]:
        """Evaluates single coin for Donchian breakout and Volume TMS confirmation."""
        # Check volume confirmation
        if volume_sma <= Decimal("0"):
            return None
        volume_ratio = volume / volume_sma
        if volume_ratio < self.volume_filter_multiplier:
            return None

        # Long breakout
        if close > upper_channel and ema_fast > ema_slow:
            # Trend Momentum Score (TMS)
            atr_safe = atr if atr > Decimal("0") else Decimal("0.0001")
            tms = ((close - ema_fast).copy_abs() / atr_safe) * volume_ratio
            return CoinCandidate(
                symbol=symbol,
                direction=1,
                tms_score=tms,
                atr=atr,
                breakout_price=close,
            )

        # Short breakout
        elif close < lower_channel and ema_fast < ema_slow:
            atr_safe = atr if atr > Decimal("0") else Decimal("0.0001")
            tms = ((close - ema_fast).copy_abs() / atr_safe) * volume_ratio
            return CoinCandidate(
                symbol=symbol,
                direction=-1,
                tms_score=tms,
                atr=atr,
                breakout_price=close,
            )

        return None

    def rank_candidates(
        self,
        long_candidates: List[CoinCandidate],
        short_candidates: List[CoinCandidate],
        current_long_count: int,
        current_short_count: int,
        btc_bullish: bool = True,
        btc_bearish: bool = True,
        cooldown_tracker: Optional[Dict[Tuple[str, int], int]] = None,
        current_bar_idx: int = 0,
    ) -> List[CoinCandidate]:
        """
        Ranks candidates by TMS, filtering by:
        1. BTC Delta filter (for altcoins).
        2. Cooldown tracker (preventing re-entries on exhausted waves).
        3. Portfolio directional caps.
        """
        filtered_longs = []
        if current_long_count < self.max_long_positions:
            for c in long_candidates:
                # Cooldown check
                if cooldown_tracker and (c.symbol, 1) in cooldown_tracker:
                    if current_bar_idx < cooldown_tracker[(c.symbol, 1)]:
                        continue

                # BTC Delta Filter: altcoins require BTC to be bullish
                if self.enable_btc_delta_filter and c.symbol != "BTCUSDT":
                    if not btc_bullish:
                        continue

                filtered_longs.append(c)

        filtered_shorts = []
        if current_short_count < self.max_short_positions:
            for c in short_candidates:
                if cooldown_tracker and (c.symbol, -1) in cooldown_tracker:
                    if current_bar_idx < cooldown_tracker[(c.symbol, -1)]:
                        continue

                # BTC Delta Filter: altcoins require BTC to be bearish
                if self.enable_btc_delta_filter and c.symbol != "BTCUSDT":
                    if not btc_bearish:
                        continue

                filtered_shorts.append(c)

        # Sort each group by TMS score descending
        filtered_longs.sort(key=lambda x: x.tms_score, reverse=True)
        filtered_shorts.sort(key=lambda x: x.tms_score, reverse=True)

        available_long_slots = max(0, self.max_long_positions - current_long_count)
        available_short_slots = max(0, self.max_short_positions - current_short_count)

        selected = filtered_longs[:available_long_slots] + filtered_shorts[:available_short_slots]
        # Return sorted by overall highest TMS
        selected.sort(key=lambda x: x.tms_score, reverse=True)
        return selected
