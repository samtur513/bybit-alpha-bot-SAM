"""
3-Stage Research Protocol Verification for Improved Trend Following Strategy
Period: 2024-10-12 to 2026-10-12 (2 Full Years)
"""

import numpy as np
import pandas as pd


def load_and_prep(symbol="ETHUSDT", lookback=168, atr_period=14):
    df = pd.read_parquet(f"data/parquet/{symbol}_1h.parquet")
    df_fund = pd.read_parquet(f"data/parquet/{symbol}_funding.parquet")
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)

    funding_map = {
        int(r["fundingRateTimestamp"]): float(r["fundingRate"])
        for _, r in df_fund.iterrows()
    }

    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    df["upper"] = high.shift(1).rolling(lookback).max()
    df["lower"] = low.shift(1).rolling(lookback).min()

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1.0 / atr_period, adjust=False).mean()

    df["ema_fast"] = close.ewm(span=50, adjust=False).mean()
    df["ema_slow"] = close.ewm(span=200, adjust=False).mean()
    df["vol_ratio"] = volume / volume.rolling(24).mean()

    return df, funding_map


def run_stage(
    df,
    funding_map,
    start_dt,
    end_dt,
    capital=300.0,
    atr_mult=3.0,
    risk_pct=0.015,
    min_qty=0.01,
    taker_fee=0.0006,
    slippage=0.0002,
):
    dates = df["datetime"]
    eval_mask = (dates >= pd.to_datetime(start_dt, utc=True)) & (
        dates < pd.to_datetime(end_dt, utc=True)
    )
    start_idx = np.where(eval_mask)[0][0]
    end_idx = np.where(eval_mask)[0][-1]

    equity = capital
    peak_equity = capital
    max_drawdown = 0.0

    position = 0
    qty = 0.0
    entry_price = 0.0
    stop_price = 0.0
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

        # Funding
        if position != 0 and ts in funding_map:
            equity -= position * qty * close * funding_map[ts]

        # Manage open position
        if position != 0:
            exit_flag = False
            exit_price = 0.0
            exit_reason = ""

            if position == 1:
                if low <= stop_price:
                    exit_flag = True
                    exit_price = min(next_open, stop_price) * (1.0 - slippage)
                    exit_reason = "stop_loss"
                elif close < ema_slow and (t - entry_bar) > 48:
                    exit_flag = True
                    exit_price = next_open * (1.0 - slippage)
                    exit_reason = "trend_exit"
                else:
                    new_stop = high - atr_mult * atr
                    if new_stop > stop_price:
                        stop_price = new_stop
            elif position == -1:
                if high >= stop_price:
                    exit_flag = True
                    exit_price = max(next_open, stop_price) * (1.0 + slippage)
                    exit_reason = "stop_loss"
                elif close > ema_slow and (t - entry_bar) > 48:
                    exit_flag = True
                    exit_price = next_open * (1.0 + slippage)
                    exit_reason = "trend_exit"
                else:
                    new_stop = low + atr_mult * atr
                    if new_stop < stop_price:
                        stop_price = new_stop

            if exit_flag:
                fee = qty * exit_price * taker_fee
                trade_pnl = (
                    qty * (exit_price - entry_price) - fee
                    if position == 1
                    else qty * (entry_price - exit_price) - fee
                )
                equity += trade_pnl
                trades.append(
                    {
                        "pnl": trade_pnl,
                        "duration": t - entry_bar,
                        "reason": exit_reason,
                    }
                )
                position = 0
                qty = 0.0

        # Entries
        if position == 0 and not np.isnan(upper) and not np.isnan(lower):
            signal = 0
            if close > upper and ema_fast > ema_slow and vol_ratio >= 1.1:
                signal = 1
            elif close < lower and ema_fast < ema_slow and vol_ratio >= 1.1:
                signal = -1

            if signal != 0:
                risk_budget = equity * risk_pct
                stop_dist = atr_mult * atr
                if stop_dist > 0:
                    raw_qty = risk_budget / stop_dist
                    max_qty = (equity * 2.5) / next_open
                    target_qty = min(raw_qty, max_qty)
                    rounded_qty = np.floor(target_qty / 0.01) * 0.01

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
                        stop_price = (
                            exec_price - stop_dist
                            if signal == 1
                            else exec_price + stop_dist
                        )

        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity * 100.0
        if dd > max_drawdown:
            max_drawdown = dd

    tdf = pd.DataFrame(trades)
    if len(tdf) > 0:
        win_trades = tdf[tdf["pnl"] > 0]
        loss_trades = tdf[tdf["pnl"] <= 0]
        gp = win_trades["pnl"].sum()
        gl = abs(loss_trades["pnl"].sum())
        pf = gp / gl if gl > 0 else 999.0
        wr = len(win_trades) / len(tdf) * 100.0
    else:
        pf, wr, gp, gl = 0, 0, 0, 0

    return {
        "final_equity": equity,
        "net_pnl": equity - capital,
        "return_pct": (equity - capital) / capital * 100.0,
        "max_drawdown": max_drawdown,
        "trades": len(tdf),
        "win_rate": wr,
        "profit_factor": pf,
    }


if __name__ == "__main__":
    df, f_map = load_and_prep("ETHUSDT", lookback=168, atr_period=14)

    print("================ 3-STAGE RESEARCH PROTOCOL (ETHUSDT) ================")
    # Stage 1: Development
    s1 = run_stage(df, f_map, "2024-10-12", "2025-10-12", capital=300.0)
    print(
        f"Stage 1 (Dev, 12M):   PnL=${s1['net_pnl']:+6.2f} ({s1['return_pct']:+5.1f}%) | PF={s1['profit_factor']:4.2f} | WR={s1['win_rate']:4.1f}% | Trades={s1['trades']} | MaxDD={s1['max_drawdown']:4.1f}%"
    )

    # Stage 2: Validation
    s2 = run_stage(df, f_map, "2025-10-12", "2026-04-12", capital=300.0)
    print(
        f"Stage 2 (Val, 6M):    PnL=${s2['net_pnl']:+6.2f} ({s2['return_pct']:+5.1f}%) | PF={s2['profit_factor']:4.2f} | WR={s2['win_rate']:4.1f}% | Trades={s2['trades']} | MaxDD={s2['max_drawdown']:4.1f}%"
    )

    # Stage 3: Final Untouched Test
    s3 = run_stage(df, f_map, "2026-04-12", "2026-10-12", capital=300.0)
    print(
        f"Stage 3 (Final, 6M):  PnL=${s3['net_pnl']:+6.2f} ({s3['return_pct']:+5.1f}%) | PF={s3['profit_factor']:4.2f} | WR={s3['win_rate']:4.1f}% | Trades={s3['trades']} | MaxDD={s3['max_drawdown']:4.1f}%"
    )

    # Full 2-Year Continuous Run
    s_full = run_stage(df, f_map, "2024-10-12", "2026-10-12", capital=300.0)
    print(
        f"Full 2-Year Run:      PnL=${s_full['net_pnl']:+6.2f} ({s_full['return_pct']:+5.1f}%) | PF={s_full['profit_factor']:4.2f} | WR={s_full['win_rate']:4.1f}% | Trades={s_full['trades']} | MaxDD={s_full['max_drawdown']:4.1f}%"
    )
