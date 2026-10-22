"""
Technical Indicators Module.
Strict adherence to trading system specifications:
- Generic EMA(period) and backward-compatible EMA(200)
- 1h Breakout Channels (N=48, 96) excluding current bar
- 1h Wilder's ATR(14) with initial SMA(14) of True Range
- 1h Wilder's RSI(14) with initial SMA(14) of gains/losses
- 4h Wilder's ADX(14) with +DM/-DM, smoothed TR, and DX smoothing
- Volume SMA(20) confirmation indicator
- 1h 20-bar exit channel
"""
from typing import Tuple, Union
import numpy as np
import pandas as pd


def calculate_ema(
    series: Union[pd.Series, pd.DataFrame], period: int
) -> pd.Series:
    """
    Calculates Exponential Moving Average (EMA) with:
    - Initial value = SMA of first `period` values (at index period - 1)
    - Alpha = 2 / (period + 1)
    - Iterative EMA: EMA_i = alpha * value_i + (1 - alpha) * EMA_{i-1}
    """
    if isinstance(series, pd.DataFrame):
        s = series["close"] if "close" in series.columns else series.iloc[:, 0]
    else:
        s = series

    n = len(s)
    ema = np.full(n, np.nan, dtype=np.float64)
    if n < period or period <= 0:
        return pd.Series(ema, index=s.index)

    values = s.to_numpy(dtype=np.float64)
    alpha = 2.0 / (period + 1.0)

    # Initial SMA of the first `period` values
    ema[period - 1] = np.mean(values[:period])

    # Iterative EMA
    for i in range(period, n):
        ema[i] = alpha * values[i] + (1.0 - alpha) * ema[i - 1]

    return pd.Series(ema, index=s.index)


def calculate_ema_200(
    series: Union[pd.Series, pd.DataFrame], period: int = 200
) -> pd.Series:
    """
    Calculates EMA(period) with:
    - Initial value = SMA of first period closes
    - Alpha = 2 / (period + 1)
    Retained for backward compatibility (delegates to calculate_ema).
    """
    return calculate_ema(series, period=period)


def calculate_wilder_atr(
    df: Union[pd.DataFrame, pd.Series], period: int = 14
) -> pd.Series:
    """
    Calculates Wilder's ATR(period) with:
    - Initial value = SMA of first period True Ranges
    - Wilder smoothing: ATR_t = (ATR_{t-1} * (period - 1) + TR_t) / period
    """
    if isinstance(df, pd.DataFrame):
        high = df["high"].to_numpy(dtype=np.float64)
        low = df["low"].to_numpy(dtype=np.float64)
        close = df["close"].to_numpy(dtype=np.float64)
        index = df.index
    else:
        high = df.to_numpy(dtype=np.float64)
        low = df.to_numpy(dtype=np.float64)
        close = df.to_numpy(dtype=np.float64)
        index = df.index

    n = len(high)
    atr = np.full(n, np.nan, dtype=np.float64)
    if n < period + 1 or period <= 0:
        return pd.Series(atr, index=index)

    # True range calculation
    tr = np.empty(n, dtype=np.float64)
    tr[0] = high[0] - low[0]
    for i in range(1, n):
        hl = high[i] - low[i]
        hc = abs(high[i] - close[i - 1])
        lc = abs(low[i] - close[i - 1])
        tr[i] = max(hl, hc, lc)

    # Initial value is SMA of first `period` TRs (starting from index 1 to period)
    # TR index 1 to period has period elements
    atr[period] = np.mean(tr[1 : period + 1])

    # Wilder's smoothing
    for i in range(period + 1, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    return pd.Series(atr, index=index)


def calculate_donchian_channel(
    df: Union[pd.DataFrame, pd.Series], lookback: int
) -> Tuple[pd.Series, pd.Series]:
    """
    Calculates Donchian channel over previous `lookback` closed bars,
    strictly EXCLUDING the current bar.
    Channel High = max(high of previous lookback bars)
    Channel Low = min(low of previous lookback bars)
    """
    if isinstance(df, pd.DataFrame):
        high = df["high"]
        low = df["low"]
    else:
        high = df
        low = df
    high_channel = high.shift(1).rolling(window=lookback, min_periods=lookback).max()
    low_channel = low.shift(1).rolling(window=lookback, min_periods=lookback).min()
    return high_channel, low_channel


def calculate_rsi(
    df: Union[pd.DataFrame, pd.Series], period: int = 14
) -> pd.Series:
    """
    Wilder's RSI(period) on close:
    - Calculate price changes: delta = close[i] - close[i-1]
    - Gains = max(delta, 0), Losses = abs(min(delta, 0))
    - First avg_gain = SMA of first `period` gains (indices 1..period)
    - First avg_loss = SMA of first `period` losses (indices 1..period)
    - Subsequent: avg_gain[i] = (avg_gain[i-1] * (period-1) + gain[i]) / period (Wilder smoothing)
    - RSI = 100 - 100 / (1 + avg_gain / avg_loss)
    - If avg_loss == 0: RSI = 100
    - Return pd.Series with NaN for first `period` indices
    """
    if isinstance(df, pd.DataFrame):
        close = df["close"].to_numpy(dtype=np.float64)
        index = df.index
    else:
        close = df.to_numpy(dtype=np.float64)
        index = df.index

    n = len(close)
    rsi = np.full(n, np.nan, dtype=np.float64)
    if n < period + 1 or period <= 0:
        return pd.Series(rsi, index=index)

    diff = np.diff(close)
    gain = np.zeros(n, dtype=np.float64)
    loss = np.zeros(n, dtype=np.float64)
    gain[1:] = np.maximum(diff, 0.0)
    loss[1:] = np.maximum(-diff, 0.0)

    avg_gain = np.full(n, np.nan, dtype=np.float64)
    avg_loss = np.full(n, np.nan, dtype=np.float64)

    # First avg_gain / avg_loss: SMA of first `period` gains/losses (indices 1..period)
    avg_gain[period] = np.mean(gain[1 : period + 1])
    avg_loss[period] = np.mean(loss[1 : period + 1])

    if avg_loss[period] == 0.0:
        rsi[period] = 100.0
    else:
        rs = avg_gain[period] / avg_loss[period]
        rsi[period] = 100.0 - (100.0 / (1.0 + rs))

    # Subsequent: Wilder smoothing
    for i in range(period + 1, n):
        avg_gain[i] = (avg_gain[i - 1] * (period - 1) + gain[i]) / period
        avg_loss[i] = (avg_loss[i - 1] * (period - 1) + loss[i]) / period

        if avg_loss[i] == 0.0:
            rsi[i] = 100.0
        else:
            rs = avg_gain[i] / avg_loss[i]
            rsi[i] = 100.0 - (100.0 / (1.0 + rs))

    return pd.Series(rsi, index=index)


def calculate_adx(
    df: Union[pd.DataFrame, pd.Series], period: int = 14
) -> pd.Series:
    """
    Wilder's ADX(period) on OHLC:
    - Calculate +DM and -DM:
      +DM = high[i] - high[i-1] if positive and > (low[i-1] - low[i]), else 0
      -DM = low[i-1] - low[i] if positive and > (high[i] - high[i-1]), else 0
    - Smooth +DM, -DM, and TR using Wilder's method (like ATR):
      first value = SMA of first `period`,
      then smoothed[i] = smoothed[i-1] - smoothed[i-1]/period + current[i]
    - +DI = 100 * smoothed_plus_dm / smoothed_tr
    - -DI = 100 * smoothed_minus_dm / smoothed_tr
    - DX = 100 * abs(+DI - -DI) / (+DI + -DI)
    - ADX = Wilder smooth of DX (first ADX = SMA of first `period` DX values, then Wilder smoothing)
    - Return pd.Series, NaN for warmup indices
    """
    if isinstance(df, pd.DataFrame):
        high = df["high"].to_numpy(dtype=np.float64)
        low = df["low"].to_numpy(dtype=np.float64)
        close = df["close"].to_numpy(dtype=np.float64)
        index = df.index
    else:
        high = df.to_numpy(dtype=np.float64)
        low = df.to_numpy(dtype=np.float64)
        close = df.to_numpy(dtype=np.float64)
        index = df.index

    n = len(high)
    adx = np.full(n, np.nan, dtype=np.float64)
    if n < 2 * period or period <= 0:
        return pd.Series(adx, index=index)

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

    smoothed_tr = np.full(n, np.nan, dtype=np.float64)
    smoothed_plus_dm = np.full(n, np.nan, dtype=np.float64)
    smoothed_minus_dm = np.full(n, np.nan, dtype=np.float64)

    # First value = SMA of first `period` (indices 1..period)
    smoothed_tr[period] = np.mean(tr[1 : period + 1])
    smoothed_plus_dm[period] = np.mean(plus_dm[1 : period + 1])
    smoothed_minus_dm[period] = np.mean(minus_dm[1 : period + 1])

    # Smoothing recursion: smoothed[i] = smoothed[i-1] - smoothed[i-1]/period + current[i]
    for i in range(period + 1, n):
        smoothed_tr[i] = smoothed_tr[i - 1] - (smoothed_tr[i - 1] / period) + tr[i]
        smoothed_plus_dm[i] = smoothed_plus_dm[i - 1] - (smoothed_plus_dm[i - 1] / period) + plus_dm[i]
        smoothed_minus_dm[i] = smoothed_minus_dm[i - 1] - (smoothed_minus_dm[i - 1] / period) + minus_dm[i]

    # Calculate +DI, -DI, and DX
    dx = np.full(n, np.nan, dtype=np.float64)
    for i in range(period, n):
        str_val = smoothed_tr[i]
        if str_val == 0.0:
            p_di = 0.0
            m_di = 0.0
        else:
            p_di = 100.0 * smoothed_plus_dm[i] / str_val
            m_di = 100.0 * smoothed_minus_dm[i] / str_val

        di_sum = p_di + m_di
        if di_sum == 0.0:
            dx[i] = 0.0
        else:
            dx[i] = 100.0 * abs(p_di - m_di) / di_sum

    # First ADX = SMA of first `period` DX values (indices period..2*period-1)
    first_adx_idx = 2 * period - 1
    adx[first_adx_idx] = np.mean(dx[period : 2 * period])

    # Subsequent ADX values: Wilder smoothing: (adx[i-1] * (period-1) + dx[i]) / period
    for i in range(2 * period, n):
        adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period

    return pd.Series(adx, index=index)


def calculate_volume_sma(
    df: Union[pd.DataFrame, pd.Series], period: int = 20
) -> pd.Series:
    """
    Simple moving average of volume column:
    df['volume'].rolling(window=period, min_periods=period).mean()
    """
    if isinstance(df, pd.DataFrame):
        vol = df["volume"]
    else:
        vol = df
    return vol.rolling(window=period, min_periods=period).mean()
