"""
Improved Systematic Crypto Trend Following (r/algotrading best practices)
- Multi-timeframe trend filter (4h EMA 50 vs EMA 200)
- Longer breakout lookback (7-day = 168h or 14-day = 336h) to avoid high-frequency chop
- Volume expansion confirmation
- Strict Taker fees (0.06%), Slippage (0.02%), and 8-hour Funding rate deductions
"""

import numpy as np
import pandas as pd


def load_data(symbol="BTCUSDT"):
    df_1h = pd.read_parquet(f"data/parquet/{symbol}_1h.parquet")
    df_fund = pd.read_parquet(f"data/parquet/{symbol}_funding.parquet")

    df_1h["datetime"] = pd.to_datetime(df_1h["timestamp"], unit="ms", utc=True)
    df_1h = df_1h.sort_values("timestamp").reset_index(drop=True)

    funding_map = {}
    for _, row in df_fund.iterrows():
        funding_map[int(row["fundingRateTimestamp"])] = float(
            row["fundingRate"]
        )

    return df_1h, funding_map


def calculate_indicators(df, channel_lookback=168, atr_period=14):
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # 1. Donchian channels (shifted by 1 to prevent lookahead)
    upper_channel = high.shift(1).rolling(channel_lookback).max()
    lower_channel = low.shift(1).rolling(channel_lookback).min()

    # 2. ATR Wilder
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / atr_period, adjust=False).mean()

    # 3. Macro Trend Filters (50 and 200 EMA on 1h, equivalent to ~12h and ~50h)
    ema_fast = close.ewm(span=50, adjust=False).mean()
    ema_slow = close.ewm(span=200, adjust=False).mean()

    # 4. Volume filter: volume > 1.2 * rolling average volume
    vol_ma = volume.rolling(24).mean()

    df["upper"] = upper_channel
    df["lower"] = lower_channel
    df["atr"] = atr
    df["ema_fast"] = ema_fast
    df["ema_slow"] = ema_slow
    df["vol_ratio"] = volume / vol_ma
    return df


def simulate_trend(
    df,
    funding_map,
    symbol="BTCUSDT",
    risk_pct=0.015,
    atr_mult=2.5,
    rr_target=3.0,
    capital=300.0,
    taker_fee=0.0006,
    slippage=0.0002,
    start_date="2024-10-12",
    end_date="2026-10-12",
    min_qty=0.001,
):
    dates = df["datetime"]
    eval_mask = (dates >= pd.to_datetime(start_date, utc=True)) & (
        dates < pd.to_datetime(end_date, utc=True)
    )
    start_idx = np.where(eval_mask)[0][0]
    end_idx = np.where(eval_mask)[0][-1]

    equity = capital
    peak_equity = capital
    max_drawdown = 0.0

    position = 0  # 1: Long, -1: Short, 0: Flat
    entry_price = 0.0
    stop_price = 0.0
    tp_price = 0.0
    qty = 0.0
    entry_bar = 0

    trades = []

    for t in range(start_idx, end_idx):
        ts = int(df["timestamp"].iloc[t])
        close = df["close"].iloc[t]
        high = df["high"].iloc[t]
        low = df["low"].iloc[t]
        upper = df["upper"].iloc[t]
        lower = df["lower"].iloc[t]
        atr = df["atr"].iloc[t]
        ema_fast = df["ema_fast"].iloc[t]
        ema_slow = df["ema_slow"].iloc[t]
        vol_ratio = df["vol_ratio"].iloc[t]

        next_open = df["open"].iloc[t + 1]

        # 1. Funding payment
        if position != 0 and ts in funding_map:
            rate = funding_map[ts]
            # Long pays positive funding, Short receives
            funding_cost = position * qty * close * rate
            equity -= funding_cost

        # 2. Manage existing position
        if position != 0:
            exit_flag = False
            exit_price = 0.0
            exit_reason = ""

            # Check trailing stop / TP on current bar
            if position == 1:
                # Long
                if low <= stop_price:
                    exit_flag = True
                    exit_price = min(next_open, stop_price) * (1.0 - slippage)
                    exit_reason = "stop_loss"
                elif high >= tp_price and tp_price > 0:
                    exit_flag = True
                    exit_price = tp_price * (1.0 - slippage)
                    exit_reason = "take_profit"
                elif close < ema_slow and (t - entry_bar) > 48:
                    # Trend breakdown exit
                    exit_flag = True
                    exit_price = next_open * (1.0 - slippage)
                    exit_reason = "trend_exit"
                else:
                    # Trailing stop update (ratchet upwards)
                    new_stop = high - atr_mult * atr
                    if new_stop > stop_price:
                        stop_price = new_stop
            elif position == -1:
                # Short
                if high >= stop_price:
                    exit_flag = True
                    exit_price = max(next_open, stop_price) * (1.0 + slippage)
                    exit_reason = "stop_loss"
                elif low <= tp_price and tp_price > 0:
                    exit_flag = True
                    exit_price = tp_price * (1.0 + slippage)
                    exit_reason = "take_profit"
                elif close > ema_slow and (t - entry_bar) > 48:
                    # Trend breakdown exit
                    exit_flag = True
                    exit_price = next_open * (1.0 + slippage)
                    exit_reason = "trend_exit"
                else:
                    # Trailing stop update (ratchet downwards)
                    new_stop = low + atr_mult * atr
                    if new_stop < stop_price:
                        stop_price = new_stop

            if exit_flag:
                fee = qty * exit_price * taker_fee
                if position == 1:
                    trade_pnl = qty * (exit_price - entry_price) - fee
                else:
                    trade_pnl = qty * (entry_price - exit_price) - fee

                equity += trade_pnl
                trades.append(
                    {
                        "entry_bar": entry_bar,
                        "exit_bar": t,
                        "duration_hours": t - entry_bar,
                        "position": position,
                        "net_pnl": trade_pnl,
                        "reason": exit_reason,
                        "equity": equity,
                    }
                )
                position = 0
                qty = 0.0

        # 3. Check new entry if flat
        if position == 0 and not np.isnan(upper) and not np.isnan(lower):
            signal = 0
            # Long: breakout above channel + trend filter (EMA fast > slow) + volume
            if close > upper and ema_fast > ema_slow and vol_ratio >= 1.1:
                signal = 1
            # Short: breakdown below channel + trend filter (EMA fast < slow) + volume
            elif close < lower and ema_fast < ema_slow and vol_ratio >= 1.1:
                signal = -1

            if signal != 0:
                risk_budget = equity * risk_pct
                stop_dist = atr_mult * atr
                if stop_dist > 0:
                    raw_qty = risk_budget / stop_dist
                    # Check maximum leverage cap (e.g. 2.5x equity)
                    max_qty = (equity * 2.5) / next_open
                    target_qty = min(raw_qty, max_qty)

                    # Round down to min_qty step
                    step = 0.001 if symbol == "BTCUSDT" else 0.01
                    rounded_qty = np.floor(target_qty / step) * step

                    if rounded_qty >= min_qty:
                        exec_price = (
                            next_open * (1.0 + slippage)
                            if signal == 1
                            else next_open * (1.0 - slippage)
                        )
                        fee = rounded_qty * exec_price * taker_fee
                        equity -= fee

                        position = signal
                        qty = rounded_qty
                        entry_price = exec_price
                        entry_bar = t + 1
                        if signal == 1:
                            stop_price = exec_price - stop_dist
                            tp_price = (
                                exec_price + rr_target * stop_dist
                                if rr_target > 0
                                else 0
                            )
                        else:
                            stop_price = exec_price + stop_dist
                            tp_price = (
                                exec_price - rr_target * stop_dist
                                if rr_target > 0
                                else 0
                            )

        # Track Drawdown
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity * 100.0
        if dd > max_drawdown:
            max_drawdown = dd

    # Stats
    tdf = pd.DataFrame(trades)
    if len(tdf) > 0:
        win_trades = tdf[tdf["net_pnl"] > 0]
        loss_trades = tdf[tdf["net_pnl"] <= 0]
        gross_profit = win_trades["net_pnl"].sum()
        gross_loss = abs(loss_trades["net_pnl"].sum())
        pf = (
            gross_profit / gross_loss
            if gross_loss > 0
            else (999.0 if gross_profit > 0 else 0.0)
        )
        wr = len(win_trades) / len(tdf) * 100.0
    else:
        pf, wr = 0.0, 0.0

    return {
        "symbol": symbol,
        "final_equity": equity,
        "net_pnl": equity - capital,
        "return_pct": (equity - capital) / capital * 100.0,
        "max_drawdown_pct": max_drawdown,
        "total_trades": len(tdf),
        "win_rate_pct": wr,
        "profit_factor": pf,
    }


if __name__ == "__main__":
    for sym in ["BTCUSDT", "ETHUSDT"]:
        print(f"\n=================== {sym} ===================")
        df, f_map = load_data(sym)
        min_q = 0.001 if sym == "BTCUSDT" else 0.01

        for lookback in [120, 168, 240]:
            df_ind = calculate_indicators(
                df.copy(), channel_lookback=lookback, atr_period=14
            )
            for mult in [2.0, 2.5, 3.0]:
                for rr in [0, 3.0]:
                    res = simulate_trend(
                        df_ind,
                        f_map,
                        symbol=sym,
                        risk_pct=0.015,
                        atr_mult=mult,
                        rr_target=rr,
                        capital=300.0,
                        start_date="2024-10-12",
                        end_date="2026-10-12",
                        min_qty=min_q,
                    )
                    tp_str = f"RR={rr}" if rr > 0 else "TrailingOnly"
                    print(
                        f"Lookback={lookback:<3} ATR_mult={mult:<3} {tp_str:<12} | Trades={res['total_trades']:<3} | WR={res['win_rate_pct']:5.1f}% | PF={res['profit_factor']:4.2f} | PnL=${res['net_pnl']:+6.2f} ({res['return_pct']:+5.1f}%) | MaxDD={res['max_drawdown_pct']:4.1f}%"
                    )
