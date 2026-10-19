"""
ETH/BTC Statistical Arbitrage (Pairs Trading) with Dynamic Kalman Filter
Isolated Experimental Research Module for Bybit Linear Perpetuals
"""

import json
from decimal import Decimal, ROUND_DOWN
import numpy as np
import pandas as pd


def load_data():
    # Load 1h bars
    btc_1h = pd.read_parquet("data/parquet/BTCUSDT_1h.parquet")
    eth_1h = pd.read_parquet("data/parquet/ETHUSDT_1h.parquet")

    # Load funding
    btc_fund = pd.read_parquet("data/parquet/BTCUSDT_funding.parquet")
    eth_fund = pd.read_parquet("data/parquet/ETHUSDT_funding.parquet")

    # Merge on timestamp
    df = pd.merge(
        btc_1h[["timestamp", "open", "high", "low", "close", "volume"]],
        eth_1h[["timestamp", "open", "high", "low", "close", "volume"]],
        on="timestamp",
        suffixes=("_btc", "_eth"),
    )
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)

    # Process funding into dict for fast lookup: (timestamp, symbol) -> rate
    funding_map = {}
    for _, row in btc_fund.iterrows():
        funding_map[(int(row["fundingRateTimestamp"]), "BTCUSDT")] = float(
            row["fundingRate"]
        )
    for _, row in eth_fund.iterrows():
        funding_map[(int(row["fundingRateTimestamp"]), "ETHUSDT")] = float(
            row["fundingRate"]
        )

    return df, funding_map


def run_kalman_filter(
    y_series, x_series, delta=1e-4, vt_val=1e-3
):
    """
    Kalman filter for dynamic hedge ratio:
    y_t = alpha_t + beta_t * x_t + e_t
    State: theta_t = [alpha, beta]^T
    """
    n = len(y_series)
    theta = np.zeros((2, n))
    e = np.zeros(n)
    q = np.zeros(n)

    # Covariance of state
    P = np.eye(2) * 1.0
    R = vt_val  # measurement noise
    Q = delta / (1 - delta) * np.eye(2)  # state noise covariance

    # Initial state
    theta[:, 0] = [y_series[0] - x_series[0], 1.0]

    for t in range(1, n):
        # Predict
        P = P + Q

        # Observation matrix F_t = [1, x_t]
        F = np.array([1.0, x_series[t]])

        # Prediction error
        y_hat = F @ theta[:, t - 1]
        err = y_series[t] - y_hat
        e[t] = err

        # Variance of prediction error
        Q_est = F @ P @ F.T + R
        q[t] = Q_est

        # Kalman gain
        K = (P @ F.T) / Q_est

        # Update state
        theta[:, t] = theta[:, t - 1] + K * err

        # Update covariance
        P = P - np.outer(K, F @ P)

    alphas = theta[0, :]
    betas = theta[1, :]
    return alphas, betas, e


def backtest_pairs(
    df,
    funding_map,
    z_entry=2.0,
    z_exit=0.2,
    z_stop=3.5,
    rolling_window=72,
    capital=300.0,
    leverage=1.5,
    taker_fee=0.0006,
    slippage=0.0002,
    start_date="2024-10-12",
    end_date="2026-10-12",
):
    """
    Event-driven simulation on 1h bars with causal execution at next bar's Open.
    """
    y = np.log(df["close_eth"].values)
    x = np.log(df["close_btc"].values)

    # 1. Kalman hedge ratio estimation
    alphas, betas, spread_err = run_kalman_filter(y, x, delta=1e-4, vt_val=1e-3)

    # 2. Spread calculation: spread_t = y_t - (alpha_t + beta_t * x_t)
    # Using rolling z-score of spread error
    spread_series = pd.Series(spread_err)
    rolling_mean = spread_series.rolling(window=rolling_window).mean()
    rolling_std = spread_series.rolling(window=rolling_window).std()
    z_scores = ((spread_series - rolling_mean) / rolling_std).values

    # Start evaluation mask
    dates = df["datetime"]
    eval_mask = (dates >= pd.to_datetime(start_date, utc=True)) & (
        dates < pd.to_datetime(end_date, utc=True)
    )
    start_idx = np.where(eval_mask)[0][0]
    end_idx = np.where(eval_mask)[0][-1]

    # Trading state
    equity = capital
    peak_equity = capital
    max_drawdown = 0.0

    position = 0  # 0: flat, 1: Long ETH / Short BTC, -1: Short ETH / Long BTC
    qty_eth = 0.0
    qty_btc = 0.0
    entry_price_eth = 0.0
    entry_price_btc = 0.0
    entry_bar = 0

    trades = []
    equity_curve = []

    for t in range(start_idx, end_idx):
        dt = df["datetime"].iloc[t]
        ts = int(df["timestamp"].iloc[t])

        # Current bar close prices for signal evaluation
        p_eth_close = df["close_eth"].iloc[t]
        p_btc_close = df["close_btc"].iloc[t]

        # Next bar open prices for execution (no lookahead!)
        if t + 1 > end_idx:
            break
        p_eth_next_open = df["open_eth"].iloc[t + 1]
        p_btc_next_open = df["open_btc"].iloc[t + 1]

        z = z_scores[t]
        beta = betas[t]

        # 1. Deduct funding if this timestamp is an 8-hour funding mark
        if position != 0:
            if (ts, "ETHUSDT") in funding_map:
                rate_eth = funding_map[(ts, "ETHUSDT")]
                # Long pays if rate > 0; Short receives if rate > 0
                funding_pnl_eth = -1.0 * position * qty_eth * p_eth_close * rate_eth
                equity += funding_pnl_eth
            if (ts, "BTCUSDT") in funding_map:
                rate_btc = funding_map[(ts, "BTCUSDT")]
                # Short pays if rate < 0; Long receives if rate < 0 (here BTC is opposite to position)
                funding_pnl_btc = (
                    -1.0 * (-position) * qty_btc * p_btc_close * rate_btc
                )
                equity += funding_pnl_btc

        # 2. Check Exits if in position
        if position != 0:
            should_exit = False
            exit_reason = ""

            # Check mean-reversion exit
            if position == 1 and z >= -z_exit:
                should_exit = True
                exit_reason = "reversion_tp"
            elif position == -1 and z <= z_exit:
                should_exit = True
                exit_reason = "reversion_tp"
            # Check stop-loss on divergence
            elif position == 1 and z <= -z_stop:
                should_exit = True
                exit_reason = "stop_loss"
            elif position == -1 and z >= z_stop:
                should_exit = True
                exit_reason = "stop_loss"
            # Time stop (e.g. 120 bars = 5 days)
            elif (t - entry_bar) >= 120:
                should_exit = True
                exit_reason = "timeout"

            if should_exit:
                # Execute exit at next Open with slippage and taker fee
                if position == 1:
                    # Closing Long ETH (Sell at open - slippage)
                    exit_eth_price = p_eth_next_open * (1.0 - slippage)
                    # Closing Short BTC (Buy at open + slippage)
                    exit_btc_price = p_btc_next_open * (1.0 + slippage)
                    pnl_eth = qty_eth * (exit_eth_price - entry_price_eth)
                    pnl_btc = qty_btc * (entry_price_btc - exit_btc_price)
                else:
                    # Closing Short ETH (Buy at open + slippage)
                    exit_eth_price = p_eth_next_open * (1.0 + slippage)
                    # Closing Long BTC (Sell at open - slippage)
                    exit_btc_price = p_btc_next_open * (1.0 - slippage)
                    pnl_eth = qty_eth * (entry_price_eth - exit_eth_price)
                    pnl_btc = qty_btc * (exit_btc_price - entry_price_btc)

                fee_eth = qty_eth * exit_eth_price * taker_fee
                fee_btc = qty_btc * exit_btc_price * taker_fee
                net_pnl = pnl_eth + pnl_btc - (fee_eth + fee_btc)

                equity += net_pnl
                trades.append(
                    {
                        "entry_bar": entry_bar,
                        "exit_bar": t + 1,
                        "duration_hours": t + 1 - entry_bar,
                        "position": position,
                        "pnl_eth": pnl_eth,
                        "pnl_btc": pnl_btc,
                        "fees": fee_eth + fee_btc,
                        "net_pnl": net_pnl,
                        "exit_reason": exit_reason,
                        "equity_after": equity,
                    }
                )

                position = 0
                qty_eth = 0.0
                qty_btc = 0.0

        # 3. Check Entries if flat
        if position == 0 and not np.isnan(z):
            entry_signal = 0
            if z <= -z_entry:
                entry_signal = 1  # Long ETH, Short BTC
            elif z >= z_entry:
                entry_signal = -1  # Short ETH, Long BTC

            if entry_signal != 0:
                # Position sizing based on equity & leverage
                # We allocate 50% notional to ETH, 50% notional to BTC
                target_notional = equity * leverage
                notional_per_leg = target_notional / 2.0

                # Execution price with slippage
                if entry_signal == 1:
                    exec_eth_price = p_eth_next_open * (1.0 + slippage)
                    exec_btc_price = p_btc_next_open * (1.0 - slippage)
                else:
                    exec_eth_price = p_eth_next_open * (1.0 - slippage)
                    exec_btc_price = p_btc_next_open * (1.0 + slippage)

                calc_qty_eth = np.floor((notional_per_leg / exec_eth_price) * 100) / 100.0
                calc_qty_btc = np.floor((notional_per_leg / exec_btc_price) * 1000) / 1000.0

                # Check Bybit minOrderQty
                if calc_qty_eth >= 0.01 and calc_qty_btc >= 0.001:
                    fee_eth = calc_qty_eth * exec_eth_price * taker_fee
                    fee_btc = calc_qty_btc * exec_btc_price * taker_fee
                    equity -= fee_eth + fee_btc

                    position = entry_signal
                    qty_eth = calc_qty_eth
                    qty_btc = calc_qty_btc
                    entry_price_eth = exec_eth_price
                    entry_price_btc = exec_btc_price
                    entry_bar = t + 1

        # Track Drawdown
        if equity > peak_equity:
            peak_equity = equity
        dd = (peak_equity - equity) / peak_equity * 100.0
        if dd > max_drawdown:
            max_drawdown = dd
        equity_curve.append(equity)

    # Compute summary stats
    trades_df = pd.DataFrame(trades)
    if len(trades_df) > 0:
        win_trades = trades_df[trades_df["net_pnl"] > 0]
        loss_trades = trades_df[trades_df["net_pnl"] <= 0]
        gross_profit = win_trades["net_pnl"].sum()
        gross_loss = abs(loss_trades["net_pnl"].sum())
        pf = (
            gross_profit / gross_loss
            if gross_loss > 0
            else (999.0 if gross_profit > 0 else 0.0)
        )
        wr = len(win_trades) / len(trades_df) * 100.0
    else:
        pf, wr, gross_profit, gross_loss = 0, 0, 0, 0

    return {
        "final_equity": equity,
        "net_pnl": equity - capital,
        "return_pct": (equity - capital) / capital * 100.0,
        "max_drawdown_pct": max_drawdown,
        "total_trades": len(trades_df),
        "win_rate_pct": wr,
        "profit_factor": pf,
        "trades_df": trades_df,
    }


if __name__ == "__main__":
    print("Loading data...")
    df, funding_map = load_data()
    print(f"Loaded {len(df)} 1h bars.")

    print("\nRunning Parameter Grid Search...")
    for z_ent in [1.5, 1.8, 2.0, 2.2]:
        for win in [48, 72, 96]:
            res = backtest_pairs(
                df,
                funding_map,
                z_entry=z_ent,
                z_exit=0.2,
                z_stop=3.2,
                rolling_window=win,
                capital=300.0,
                leverage=1.5,
                start_date="2024-10-12",
                end_date="2026-10-12",
            )
            print(
                f"Z_entry={z_ent:<4} Win={win:<3} | Trades={res['total_trades']:<3} | WR={res['win_rate_pct']:5.1f}% | PF={res['profit_factor']:4.2f} | PnL=${res['net_pnl']:+6.2f} ({res['return_pct']:+5.1f}%) | MaxDD={res['max_drawdown_pct']:4.1f}%"
            )
