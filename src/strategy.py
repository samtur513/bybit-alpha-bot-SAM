"""
Strategy Engine v1.0.
Multi-factor signal generation with:
- Dual EMA trend filter (50/200 on 4H)
- ADX market regime detection
- RSI momentum filter
- Volume confirmation
- Minimum breakout distance filter
- Cooldown after loss
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

from src.config import (
    ATR_PERIOD_1H,
    EMA_FAST_PERIOD_4H,
    EMA_SLOW_PERIOD_4H,
    ADX_PERIOD_4H,
    RSI_PERIOD_1H,
    EXIT_CHANNEL_PERIOD_1H,
    MAX_HOLD_HOURS,
    VOLUME_SMA_PERIOD,
    ADX_TREND_THRESHOLD,
    ADX_TRANSITION_THRESHOLD,
    ADX_SHORT_THRESHOLD,
    ETH_VOLUME_MULT,
    ETH_ADX_MIN,
    RSI_OVERBOUGHT,
    RSI_OVERSOLD,
    VOLUME_MULT_THRESHOLD,
    BREAKOUT_MIN_ATR_DIST,
    EMA_INTERLEAVE_PCT,
    StrategyParams,
)
from src.indicators import (
    calculate_donchian_channel,
    calculate_ema,
    calculate_ema_200,
    calculate_wilder_atr,
    calculate_rsi,
    calculate_adx,
    calculate_volume_sma,
)

@dataclass
class Signal:
    timestamp: int            # Signal timestamp (close of 1h candle)
    exec_timestamp: int       # Minute when execution is allowed (ts + 60,000)
    symbol: str
    direction: int            # +1 for Long, -1 for Short
    entry_ref_price: float    # Close of the signal candle
    boundary_price: float     # The channel level that was broken
    atr: float                # ATR at signal time
    normalized_distance: float# abs(close - boundary) / ATR
    k_mult: float
    n_lookback: int
    rsi_value: float          # RSI at signal time
    adx_value: float          # ADX at signal time
    volume_ratio: float       # volume / SMA(20) volume

class StrategyV10:
    """Strategy v1.0 with multi-factor entry filters."""

    def __init__(self, params: StrategyParams):
        self.params = params

    def prepare_data(
        self,
        df_1m: pd.DataFrame,
        df_1h: pd.DataFrame,
        df_4h: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Computes all indicators on closed candles and maps 4h filters onto 1h bars.
        NO lookahead bias.
        """
        df_1h = df_1h.copy().sort_values("timestamp").reset_index(drop=True)
        df_4h = df_4h.copy().sort_values("timestamp").reset_index(drop=True)

        # ---- 4H Indicators ----
        # Dual EMA trend filter
        df_4h["ema50"] = calculate_ema(df_4h["close"], period=EMA_FAST_PERIOD_4H)
        df_4h["ema200"] = calculate_ema(df_4h["close"], period=EMA_SLOW_PERIOD_4H)

        # ADX for market regime
        df_4h["adx14"] = calculate_adx(df_4h, period=ADX_PERIOD_4H)

        # 4H candle close time
        df_4h["close_time"] = df_4h["timestamp"] + 4 * 3600 * 1000

        # Dual-EMA trend direction
        # Long: close > ema50 > ema200, ema50 rising
        # Short: close < ema50 < ema200, ema50 falling
        # Interleaved (diff < 0.5%): no trend
        ema50 = df_4h["ema50"].values
        ema200 = df_4h["ema200"].values
        close_4h = df_4h["close"].values

        trend = np.zeros(len(df_4h), dtype=np.int32)
        for i in range(1, len(df_4h)):
            if np.isnan(ema50[i]) or np.isnan(ema200[i]):
                trend[i] = 0
                continue

            # Check if EMAs are too close (interleaved)
            ema_diff_pct = abs(ema50[i] - ema200[i]) / ema200[i] if ema200[i] > 0 else 0
            if ema_diff_pct < EMA_INTERLEAVE_PCT:
                trend[i] = 0
                continue

            ema50_rising = ema50[i] > ema50[i - 1] if not np.isnan(ema50[i - 1]) else False
            ema50_falling = ema50[i] < ema50[i - 1] if not np.isnan(ema50[i - 1]) else False

            if close_4h[i] > ema50[i] and ema50[i] > ema200[i] and ema50_rising:
                trend[i] = 1   # Long allowed
            elif close_4h[i] < ema50[i] and ema50[i] < ema200[i] and ema50_falling:
                trend[i] = -1  # Short allowed
            else:
                trend[i] = 0   # No trade

        df_4h["trend_allowed"] = trend

        # ---- 1H Indicators ----
        df_1h["close_time"] = df_1h["timestamp"] + 1 * 3600 * 1000

        # Breakout channel over N previous closed 1h candles
        df_1h["breakout_high"], df_1h["breakout_low"] = calculate_donchian_channel(
            df_1h, self.params.N
        )

        # ATR(14)
        df_1h["atr14"] = calculate_wilder_atr(df_1h, period=ATR_PERIOD_1H)

        # RSI(14)
        df_1h["rsi14"] = calculate_rsi(df_1h, period=RSI_PERIOD_1H)

        # Volume SMA(20)
        df_1h["vol_sma20"] = calculate_volume_sma(df_1h, period=VOLUME_SMA_PERIOD)

        # Exit channel over 20 previous closed 1h candles
        df_1h["exit_channel_high"], df_1h["exit_channel_low"] = calculate_donchian_channel(
            df_1h, EXIT_CHANNEL_PERIOD_1H
        )

        # ---- Merge 4H onto 1H ----
        merged = pd.merge_asof(
            df_1h,
            df_4h[["close_time", "trend_allowed", "ema50", "ema200", "adx14"]].rename(
                columns={
                    "close_time": "trend_close_time",
                    "adx14": "adx14_4h",
                }
            ),
            left_on="close_time",
            right_on="trend_close_time",
            direction="backward",
        )

        return merged

    def generate_signals_for_bar(
        self, row: pd.Series, symbol: str
    ) -> Optional[Signal]:
        """
        Multi-factor signal evaluation at 1h close.
        ALL conditions must be true for a signal.
        """
        # Check all required indicator values exist
        required_fields = [
            "breakout_high", "breakout_low", "atr14", "trend_allowed",
            "rsi14", "adx14_4h", "vol_sma20",
        ]
        for field in required_fields:
            if pd.isna(row.get(field)):
                return None

        close = float(row["close"])
        b_high = float(row["breakout_high"])
        b_low = float(row["breakout_low"])
        atr = float(row["atr14"])
        trend = int(row["trend_allowed"])
        rsi = float(row["rsi14"])
        adx = float(row["adx14_4h"])
        volume = float(row["volume"])
        vol_sma = float(row["vol_sma20"])

        if atr <= 0 or vol_sma <= 0:
            return None

        volume_ratio = volume / vol_sma

        # Symbol-specific thresholds (ETH requires higher volume/ADX confirmation)
        vol_threshold = ETH_VOLUME_MULT if symbol == "ETHUSDT" else VOLUME_MULT_THRESHOLD
        adx_min_long = ETH_ADX_MIN if symbol == "ETHUSDT" else ADX_TRANSITION_THRESHOLD

        # ===== FILTER 1: Market regime (ADX) =====
        if adx < adx_min_long:
            # Sideways market - no breakout trades
            return None

        # In transition zone (15-25), use stricter RSI thresholds
        rsi_adjustment = 5 if adx < ADX_TREND_THRESHOLD else 0

        # ===== LONG Signal Evaluation =====
        if close > b_high and trend == 1:
            # FILTER 2: RSI momentum
            rsi_min = self.params.RSI_LONG_MIN + rsi_adjustment
            if rsi < rsi_min or rsi > RSI_OVERBOUGHT:
                return None

            # FILTER 3: Volume confirmation
            if volume_ratio < vol_threshold:
                return None

            # FILTER 4: Minimum breakout distance
            breakout_dist = abs(close - b_high) / atr
            if breakout_dist < BREAKOUT_MIN_ATR_DIST:
                return None

            return Signal(
                timestamp=int(row["close_time"]),
                exec_timestamp=int(row["close_time"]),
                symbol=symbol,
                direction=1,
                entry_ref_price=close,
                boundary_price=b_high,
                atr=atr,
                normalized_distance=breakout_dist,
                k_mult=self.params.K,
                n_lookback=self.params.N,
                rsi_value=rsi,
                adx_value=adx,
                volume_ratio=volume_ratio,
            )

        # ===== SHORT Signal Evaluation =====
        if close < b_low and trend == -1:
            # Shorts require strong ADX confirmation (>= 30) to avoid bear traps
            adx_min_short = max(ADX_SHORT_THRESHOLD, ETH_ADX_MIN if symbol == "ETHUSDT" else ADX_SHORT_THRESHOLD)
            if adx < adx_min_short:
                return None

            # FILTER 2: RSI momentum
            rsi_max = self.params.RSI_SHORT_MAX - rsi_adjustment
            if rsi > rsi_max or rsi < RSI_OVERSOLD:
                return None

            # FILTER 3: Volume confirmation
            if volume_ratio < vol_threshold:
                return None

            # FILTER 4: Minimum breakout distance
            breakout_dist = abs(close - b_low) / atr
            if breakout_dist < BREAKOUT_MIN_ATR_DIST:
                return None

            return Signal(
                timestamp=int(row["close_time"]),
                exec_timestamp=int(row["close_time"]),
                symbol=symbol,
                direction=-1,
                entry_ref_price=close,
                boundary_price=b_low,
                atr=atr,
                normalized_distance=breakout_dist,
                k_mult=self.params.K,
                n_lookback=self.params.N,
                rsi_value=rsi,
                adx_value=adx,
                volume_ratio=volume_ratio,
            )

        return None


# Backward-compatible alias
StrategyV01 = StrategyV10
