"""
Market Regime Classifier Module v2.1 for Bybit Multi-Alpha System.
Strict Zero-Lookahead Implementation:
- Evaluates regime at bar close (t) using only past closed bars (shift(1) channels).
- Classifies into:
    * CHOP / RANGE: ADX(14) < 20 OR Volatility Compression (BB Width percentile <= 40th percentile)
    * TREND: ADX(14) >= 20 AND Volume > Volume SMA(20)
    * NEUTRAL: Transitional regime
- Computes Bollinger Bands (20, 2.0), Keltner Channels (20, 1.5 ATR), Wilder RSI(14), Wilder ADX(14), and Wilder ATR(14).
"""

from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional, Tuple, Union
import numpy as np
import pandas as pd


class MarketRegime(str, Enum):
    RANGE = "RANGE"      # Flat / Chop regime
    TREND = "TREND"      # Trending impulse regime
    NEUTRAL = "NEUTRAL"  # Transitional state


@dataclass
class RegimeData:
    symbol: str
    regime: MarketRegime
    adx: float
    bb_mid: float
    bb_upper: float
    bb_lower: float
    bb_width: float
    bb_width_pctl: float
    kc_upper: float
    kc_lower: float
    rsi: float
    atr: float
    volume: float
    volume_sma: float
    is_volatility_compressed: bool
    is_chop: bool
    is_trend: bool

    @property
    def allows_range_trading(self) -> bool:
        """Range submodule activates in CHOP/RANGE when not in strong TREND."""
        return self.is_chop and not self.is_trend

    @property
    def allows_trend_trading(self) -> bool:
        """Trend module activates in strong TREND (ADX >= 20 and volume confirmation)."""
        return self.is_trend


class MarketRegimeClassifier:
    """
    Quantitative Regime Classifier for 1h Bybit Futures.
    Guarantees zero-lookahead bias.
    """

    def __init__(
        self,
        adx_trend_threshold: float = 20.0,
        bbw_compression_percentile: float = 40.0,
        bb_period: int = 20,
        bb_std_mult: float = 2.0,
        kc_atr_mult: float = 1.5,
        atr_period: int = 14,
        rsi_period: int = 14,
        rsi_oversold: float = 35.0,
        rsi_overbought: float = 65.0,
        volume_sma_period: int = 20,
        percentile_lookback: int = 120,
    ):
        self.adx_trend_threshold = adx_trend_threshold
        self.bbw_compression_percentile = bbw_compression_percentile
        self.bb_period = bb_period
        self.bb_std_mult = bb_std_mult
        self.kc_atr_mult = kc_atr_mult
        self.atr_period = atr_period
        self.rsi_period = rsi_period
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.volume_sma_period = volume_sma_period
        self.percentile_lookback = percentile_lookback

    @staticmethod
    def compute_wilder_adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Vectorized Wilder's ADX(period)."""
        high = df["high"].to_numpy(dtype=np.float64)
        low = df["low"].to_numpy(dtype=np.float64)
        close = df["close"].to_numpy(dtype=np.float64)
        n = len(close)
        adx = np.full(n, np.nan, dtype=np.float64)
        if n < 2 * period or period <= 0:
            return pd.Series(adx, index=df.index)

        tr = np.zeros(n, dtype=np.float64)
        plus_dm = np.zeros(n, dtype=np.float64)
        minus_dm = np.zeros(n, dtype=np.float64)

        tr[0] = high[0] - low[0]
        for i in range(1, n):
            hl = high[i] - low[i]
            hc = abs(high[i] - close[i - 1])
            lc = abs(low[i] - close[i - 1])
            tr[i] = max(hl, hc, lc)

            up = high[i] - high[i - 1]
            down = low[i - 1] - low[i]
            plus_dm[i] = up if (up > 0.0 and up > down) else 0.0
            minus_dm[i] = down if (down > 0.0 and down > up) else 0.0

        s_tr = np.full(n, np.nan, dtype=np.float64)
        s_p_dm = np.full(n, np.nan, dtype=np.float64)
        s_m_dm = np.full(n, np.nan, dtype=np.float64)

        s_tr[period] = np.mean(tr[1 : period + 1])
        s_p_dm[period] = np.mean(plus_dm[1 : period + 1])
        s_m_dm[period] = np.mean(minus_dm[1 : period + 1])

        for i in range(period + 1, n):
            s_tr[i] = s_tr[i - 1] - (s_tr[i - 1] / period) + tr[i] / period
            s_p_dm[i] = s_p_dm[i - 1] - (s_p_dm[i - 1] / period) + plus_dm[i] / period
            s_m_dm[i] = s_m_dm[i - 1] - (s_m_dm[i - 1] / period) + minus_dm[i] / period

        dx = np.full(n, np.nan, dtype=np.float64)
        for i in range(period, n):
            st = s_tr[i]
            if st == 0.0:
                p_di = 0.0
                m_di = 0.0
            else:
                p_di = 100.0 * s_p_dm[i] / st
                m_di = 100.0 * s_m_dm[i] / st
            di_sum = p_di + m_di
            dx[i] = 100.0 * abs(p_di - m_di) / di_sum if di_sum > 0.0 else 0.0

        adx[2 * period - 1] = np.mean(dx[period : 2 * period])
        for i in range(2 * period, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

        return pd.Series(adx, index=df.index)

    @staticmethod
    def compute_wilder_rsi(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Vectorized Wilder's RSI(period)."""
        close = df["close"].to_numpy(dtype=np.float64)
        n = len(close)
        rsi = np.full(n, np.nan, dtype=np.float64)
        if n < period + 1 or period <= 0:
            return pd.Series(rsi, index=df.index)

        diff = np.diff(close)
        gain = np.zeros(n, dtype=np.float64)
        loss = np.zeros(n, dtype=np.float64)
        gain[1:] = np.maximum(diff, 0.0)
        loss[1:] = np.maximum(-diff, 0.0)

        avg_gain = np.full(n, np.nan, dtype=np.float64)
        avg_loss = np.full(n, np.nan, dtype=np.float64)
        avg_gain[period] = np.mean(gain[1 : period + 1])
        avg_loss[period] = np.mean(loss[1 : period + 1])

        if avg_loss[period] == 0.0:
            rsi[period] = 100.0
        else:
            rs = avg_gain[period] / avg_loss[period]
            rsi[period] = 100.0 - (100.0 / (1.0 + rs))

        for i in range(period + 1, n):
            avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i]) / period
            avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i]) / period
            if avg_loss[i] == 0.0:
                rsi[i] = 100.0
            else:
                rs = avg_gain[i] / avg_loss[i]
                rsi[i] = 100.0 - (100.0 / (1.0 + rs))

        return pd.Series(rsi, index=df.index)

    @staticmethod
    def compute_wilder_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Vectorized Wilder's ATR(period)."""
        high = df["high"].to_numpy(dtype=np.float64)
        low = df["low"].to_numpy(dtype=np.float64)
        close = df["close"].to_numpy(dtype=np.float64)
        n = len(close)
        atr = np.full(n, np.nan, dtype=np.float64)
        if n < period + 1 or period <= 0:
            return pd.Series(atr, index=df.index)

        tr = np.empty(n, dtype=np.float64)
        tr[0] = high[0] - low[0]
        for i in range(1, n):
            hl = high[i] - low[i]
            hc = abs(high[i] - close[i - 1])
            lc = abs(low[i] - close[i - 1])
            tr[i] = max(hl, hc, lc)

        atr[period] = np.mean(tr[1 : period + 1])
        for i in range(period + 1, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

        return pd.Series(atr, index=df.index)

    def prepare_indicators(self, df_1h: pd.DataFrame) -> pd.DataFrame:
        """
        Calculates all indicators strictly respecting zero-lookahead bias.
        All channels and indicators are calculated on completed bars.
        """
        df = df_1h.copy().sort_values("timestamp").reset_index(drop=True)
        close = df["close"]
        volume = df["volume"]

        # Base technical indicators
        df["atr"] = self.compute_wilder_atr(df, self.atr_period)
        df["rsi"] = self.compute_wilder_rsi(df, self.rsi_period)
        df["adx"] = self.compute_wilder_adx(df, self.atr_period)

        # Bollinger Bands on shift(1) or bar t close
        # Using shift(1) for limit order boundary execution ensures bar t+1 knows bounds from t
        df["bb_mid"] = close.shift(1).rolling(window=self.bb_period).mean()
        df["bb_std"] = close.shift(1).rolling(window=self.bb_period).std(ddof=0)
        df["bb_upper"] = df["bb_mid"] + self.bb_std_mult * df["bb_std"]
        df["bb_lower"] = df["bb_mid"] - self.bb_std_mult * df["bb_std"]

        # Bollinger Band Width & Rolling Percentile Rank
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
        df["bb_width_pctl"] = (
            df["bb_width"].rolling(window=self.percentile_lookback, min_periods=self.bb_period).rank(pct=True) * 100.0
        )

        # Keltner Channels on shift(1)
        shift_atr = df["atr"].shift(1)
        df["kc_upper"] = df["bb_mid"] + self.kc_atr_mult * shift_atr
        df["kc_lower"] = df["bb_mid"] - self.kc_atr_mult * shift_atr

        # Volume SMA
        df["volume_sma"] = volume.shift(1).rolling(window=self.volume_sma_period).mean()

        return df

    def classify_bar(
        self,
        symbol: str,
        adx: float,
        bb_mid: float,
        bb_upper: float,
        bb_lower: float,
        bb_width_pctl: float,
        kc_upper: float,
        kc_lower: float,
        rsi: float,
        atr: float,
        volume: float,
        volume_sma: float,
    ) -> RegimeData:
        """
        Classifies market regime for a single bar at close.
        Zero Lookahead: relies solely on closed-bar values.
        """
        bb_width = (bb_upper - bb_lower) / bb_mid if bb_mid > 0 else 0.0
        is_compressed = bool(np.isfinite(bb_width_pctl) and bb_width_pctl <= self.bbw_compression_percentile)

        # Trend condition: ADX >= 20 and Volume > 20 SMA
        is_trend = bool(
            np.isfinite(adx)
            and adx >= self.adx_trend_threshold
            and np.isfinite(volume)
            and np.isfinite(volume_sma)
            and volume > volume_sma
        )

        # Chop condition: ADX < 20 OR Volatility Compression
        is_chop = bool((np.isfinite(adx) and adx < self.adx_trend_threshold) or is_compressed)

        if is_trend:
            regime = MarketRegime.TREND
        elif is_chop:
            regime = MarketRegime.RANGE
        else:
            regime = MarketRegime.NEUTRAL

        return RegimeData(
            symbol=symbol,
            regime=regime,
            adx=float(adx) if np.isfinite(adx) else 0.0,
            bb_mid=float(bb_mid) if np.isfinite(bb_mid) else 0.0,
            bb_upper=float(bb_upper) if np.isfinite(bb_upper) else 0.0,
            bb_lower=float(bb_lower) if np.isfinite(bb_lower) else 0.0,
            bb_width=float(bb_width) if np.isfinite(bb_width) else 0.0,
            bb_width_pctl=float(bb_width_pctl) if np.isfinite(bb_width_pctl) else 50.0,
            kc_upper=float(kc_upper) if np.isfinite(kc_upper) else 0.0,
            kc_lower=float(kc_lower) if np.isfinite(kc_lower) else 0.0,
            rsi=float(rsi) if np.isfinite(rsi) else 50.0,
            atr=float(atr) if np.isfinite(atr) else 0.0,
            volume=float(volume) if np.isfinite(volume) else 0.0,
            volume_sma=float(volume_sma) if np.isfinite(volume_sma) else 0.0,
            is_volatility_compressed=is_compressed,
            is_chop=is_chop,
            is_trend=is_trend,
        )
