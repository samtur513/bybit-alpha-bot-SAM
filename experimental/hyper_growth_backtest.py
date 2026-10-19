"""
Hyper-Growth Quantitative Engine:
Testing compounding acceleration methods from r/algotrading:
1. Dynamic Compounding (continuous equity reinvestment)
2. Risk Scaling: 2.0%, 3.0%, 4.0% per trade (Fractional Kelly)
3. Multi-asset universe (BTC + ETH combined concurrent trading)
4. Anti-Martingale Pyramiding (adding to winners at breakeven)
"""

from decimal import Decimal
import numpy as np
import pandas as pd


def load_dataset(symbol):
    df_1h = pd.read_parquet(f"data/parquet/{symbol}_1h.parquet")
    df_fund = pd.read_parquet(f"data/parquet/{symbol}_funding.parquet")
    df_1h["datetime"] = pd.to_datetime(df_1h["timestamp"], unit="ms", utc=True)
    df_1h = df_1h.sort_values("timestamp").reset_index(drop=True)

    funding_map = {
        int(r["fundingRateTimestamp"]): float(r["fundingRate"])
        for _, r in df_fund.iterrows()
    }

    # Indicators: N=120, ATR=14, EMA=50/200, Volume SMA=20
    close = df_1h["close"]
    high = df_1h["high"]
    low = df_1h["low"]
    volume = df_1h["volume"]

    df_1h["upper"] = high.shift(1).rolling(120).max()
    df_1h["lower"] = low.shift(1).rolling(120).min()

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df_1h["atr"] = tr.ewm(alpha=1.0 / 14, adjust=False).mean()

    df_1h["ema_fast"] = close.ewm(span=50, adjust=False).mean()
    df_1h["ema_slow"] = close.ewm(span=200, adjust=False).mean()
    df_1h["vol_ratio"] = volume / volume.rolling(24).mean()

    return df_1h, funding_map


def run_growth_sim(
    btc_data,
    eth_data,
    start_dt="2024-10-12",
    end_dt="2025-02-12",  # 4 months!
    capital=300.0,
    risk_pct=0.03,  # 3% risk per trade
    max_leverage=5.0,  # 5x max leverage
    atr_mult=2.5,
    allow_pyramiding=True,
    taker_fee=0.0006,
    slippage=0.0002,
):
    df_btc, fund_btc = btc_data
    df_eth, fund_eth = eth_data

    # Synchronize timestamps
    ts_common = np.intersect1d(df_btc["timestamp"], df_eth["timestamp"])
    df_b = (
        df_btc[df_btc["timestamp"].isin(ts_common)]
        .sort_values("timestamp")
        .reset_index(drop=True)
    )
    df_e = (
        df_eth[df_eth["timestamp"].isin(ts_common)]
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    dates = df_b["datetime"]
    eval_mask = (dates >= pd.to_datetime(start_dt, utc=True)) & (
        dates < pd.to_datetime(end_dt, utc=True)
    )
    start_idx = np.where(eval_mask)[0][0]
    end_idx = np.where(eval_mask)[0][-1]

    equity = capital
    peak_equity = capital
    max_drawdown = 0.0

    # Active positions dict: sym -> state
    positions = {}

    trades = []
    equity_history = []

    for t in range(start_idx, end_idx):
        for sym, df_curr, f_map in [
            ("BTCUSDT", df_b, fund_btc),
            ("ETHUSDT", df_e, fund_eth),
        ]:
            ts = int(df_curr["timestamp"].iloc[t])
            close = df_curr["close"].iloc[t]
            high = df_curr["high"].iloc[t]
            low = df_curr["low"].iloc[t]
            upper = df_curr["upper"].iloc[t]
            lower = df_curr["lower"].iloc[t]
            atr = df_curr["atr"].iloc[t]
            ema_f = df_curr["ema_fast"].iloc[t]
            ema_s = df_curr["ema_slow"].iloc[t]
            vol_r = df_curr["vol_ratio"].iloc[t]
            next_open = df_curr["open"].iloc[t + 1]

            # Funding deduction
            if sym in positions and ts in f_map:
                pos = positions[sym]
                equity -= pos["qty"] * close * pos["dir"] * f_map[ts]

            # Position management
            if sym in positions:
                pos = positions[sym]
                exit_flag = False
                exit_price = 0.0

                if pos["dir"] == 1:
                    # Long stop
                    if low <= pos["stop"]:
                        exit_flag = True
                        exit_price = min(next_open, pos["stop"]) * (
                            1.0 - slippage
                        )
                    else:
                        # Ratchet trailing stop
                        new_stop = high - atr_mult * atr
                        if new_stop > pos["stop"]:
                            pos["stop"] = new_stop
                        # Pyramiding: if price gained > 2 ATR and not pyramided yet
                        if (
                            allow_pyramiding
                            and not pos["pyramided"]
                            and (high - pos["entry_price"]) >= 2.0 * atr
                        ):
                            pos["stop"] = (
                                pos["entry_price"]
                            )  # move stop to breakeven
                            # Add 50% more size financed by open profit
                            add_qty = pos["qty"] * 0.5
                            step = 0.001 if sym == "BTCUSDT" else 0.01
                            add_qty = np.floor(add_qty / step) * step
                            if add_qty >= step:
                                fee = add_qty * next_open * taker_fee
                                equity -= fee
                                pos["qty"] += add_qty
                                pos["pyramided"] = True
                else:
                    # Short stop
                    if high >= pos["stop"]:
                        exit_flag = True
                        exit_price = max(next_open, pos["stop"]) * (
                            1.0 + slippage
                        )
                    else:
                        new_stop = low + atr_mult * atr
                        if new_stop < pos["stop"]:
                            pos["stop"] = new_stop
                        if (
                            allow_pyramiding
                            and not pos["pyramided"]
                            and (pos["entry_price"] - low) >= 2.0 * atr
                        ):
                            pos["stop"] = pos["entry_price"]
                            add_qty = pos["qty"] * 0.5
                            step = 0.001 if sym == "BTCUSDT" else 0.01
                            add_qty = np.floor(add_qty / step) * step
                            if add_qty >= step:
                                fee = add_qty * next_open * taker_fee
                                equity -= fee
                                pos["qty"] += add_qty
                                pos["pyramided"] = True

                if exit_flag:
                    fee = pos["qty"] * exit_price * taker_fee
                    trade_pnl = (
                        pos["qty"] * (exit_price - pos["entry_price"]) - fee
                        if pos["dir"] == 1
                        else pos["qty"] * (pos["entry_price"] - exit_price)
                        - fee
                    )
                    equity += trade_pnl
                    trades.append(
                        {
                            "sym": sym,
                            "pnl": trade_pnl,
                            "ret": trade_pnl / equity * 100,
                        }
                    )
                    del positions[sym]

            # Entry check
            if sym not in positions and not np.isnan(upper):
                sig = 0
                if close > upper and ema_f > ema_s and vol_r >= 1.2:
                    sig = 1
                elif close < lower and ema_f < ema_s and vol_r >= 1.2:
                    sig = -1

                if sig != 0 and equity > 10.0:
                    risk_budget = equity * risk_pct
                    stop_dist = atr_mult * atr
                    if stop_dist > 0:
                        raw_qty = risk_budget / stop_dist
                        max_allowed_qty = (equity * max_leverage) / next_open
                        target_qty = min(raw_qty, max_allowed_qty)

                        step = 0.001 if sym == "BTCUSDT" else 0.01
                        rounded_qty = np.floor(target_qty / step) * step

                        if rounded_qty >= step:
                            exec_p = (
                                next_open * (1.0 + slippage)
                                if sig == 1
                                else next_open * (1.0 - slippage)
                            )
                            fee = rounded_qty * exec_p * taker_fee
                            equity -= fee

                            positions[sym] = {
                                "dir": sig,
                                "qty": rounded_qty,
                                "entry_price": exec_p,
                                "stop": exec_p - stop_dist
                                if sig == 1
                                else exec_p + stop_dist,
                                "pyramided": False,
                            }

        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity * 100.0
        if dd > max_drawdown:
            max_drawdown = dd
        equity_history.append(equity)

    tdf = pd.DataFrame(trades)
    win_rate = (len(tdf[tdf["pnl"] > 0]) / len(tdf) * 100.0) if len(tdf) > 0 else 0
    return {
        "final_equity": equity,
        "net_pnl": equity - capital,
        "return_pct": (equity - capital) / capital * 100.0,
        "max_drawdown": max_drawdown,
        "total_trades": len(tdf),
        "win_rate": win_rate,
    }


if __name__ == "__main__":
    print("Loading data for BTC and ETH...")
    btc = load_dataset("BTCUSDT")
    eth = load_dataset("ETHUSDT")

    print("\n--- Testing 4-Month High-Momentum Windows (Start: $300) ---")
    windows = [
        ("Bull/Rally Window (2024-10-12 to 2025-02-12)", "2024-10-12", "2025-02-12"),
        ("Spring Window (2025-02-12 to 2025-06-12)", "2025-02-12", "2025-06-12"),
        ("Summer/Fall (2025-06-12 to 2025-10-12)", "2025-06-12", "2025-10-12"),
        ("Winter Rally (2025-10-12 to 2026-02-12)", "2025-10-12", "2026-02-12"),
        ("Spring 2026 (2026-02-12 to 2026-06-12)", "2026-02-12", "2026-06-12"),
    ]

    for label, s_dt, e_dt in windows:
        print(f"\n{label}:")
        for risk, lev in [(0.02, 3.0), (0.03, 5.0), (0.05, 7.0)]:
            res = run_growth_sim(
                btc,
                eth,
                start_dt=s_dt,
                end_dt=e_dt,
                capital=300.0,
                risk_pct=risk,
                max_leverage=lev,
                allow_pyramiding=True,
            )
            print(
                f"  Risk={risk*100:3.1f}% MaxLev={lev:2.0f}x | Final=${res['final_equity']:6.2f} ({res['return_pct']:+6.1f}%) | MaxDD={res['max_drawdown']:4.1f}% | Trades={res['total_trades']:<2} | WR={res['win_rate']:4.1f}%"
            )
