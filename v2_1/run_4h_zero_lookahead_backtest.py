"""
4h Timeframe Zero-Lookahead Backtester for Multi-Alpha Growth Engine v2.1 (Improvement I1 / I7).
Data: Bybit 18 symbols from 2024-01-01 to 2026-10-22.

Zero-lookahead protocol:
- Resample 1h to 4h: Open=first, High=max, Low=min, Close=last, Volume=sum.
- Signals evaluated strictly at 4h bar close (t) with causal shift(1) Donchian 30.
- Execution strictly at 4h bar open (t+1) with 0.02% slippage and 0.09% taker fee.
- Weighted average pyramiding add-on (+50% at +2 ATR) with true basket breakeven.
- Two-tier Circuit Breaker: Soft de-risking at 15% DD (cuts risk by 50%), Hard halt at 30% DD.
- Altcoin LONG-only bias / BTC Delta Trend Filter.
"""

from decimal import Decimal
import logging
from pathlib import Path
import sys
import time
from typing import Dict, List, Tuple
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2_1.src.config import (
    DEFAULT_SYMBOLS,
    INITIAL_CAPITAL,
    BASE_TAKER_FEE,
    BASE_SLIPPAGE,
    PORTFOLIO_LEVERAGE,
)
from v2_1.src.core_v2 import CoreV2Engine, PortfolioSnapshot

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("4hBacktestV21")

DATA_DIR = REPO_ROOT / "data" / "parquet_2024_2026"


def resample_and_compute_4h_indicators(
    df_1h: pd.DataFrame,
    donchian_window: int = 30,
    atr_period: int = 14,
) -> pd.DataFrame:
    df = df_1h.copy().sort_values("timestamp").reset_index(drop=True)
    df["dt"] = pd.to_datetime(df["timestamp"], unit="ms")

    # Resample to 4-hour candles
    df_4h = (
        df.resample("4h", on="dt")
        .agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "timestamp": "first",
        })
        .dropna()
        .reset_index(drop=True)
    )

    close = df_4h["close"]
    high = df_4h["high"]
    low = df_4h["low"]
    volume = df_4h["volume"]

    # Strict Zero-Lookahead: shift(1) ensures bar t's high/low are not in its own channel
    df_4h["upper"] = high.shift(1).rolling(donchian_window).max()
    df_4h["lower"] = low.shift(1).rolling(donchian_window).min()

    # Wilder ATR
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df_4h["atr"] = tr.ewm(alpha=1.0 / atr_period, adjust=False).mean()

    # Trend and Volume confirmation
    df_4h["ema_fast"] = close.ewm(span=50, adjust=False).mean()
    df_4h["ema_slow"] = close.ewm(span=200, adjust=False).mean()
    df_4h["volume_sma"] = volume.rolling(20).mean()

    return df_4h


def run_4h_simulation(
    base_risk_pct: Decimal = Decimal("0.01"),
    choppy_risk_pct: Decimal = Decimal("0.003"),
    enable_btc_filter: bool = True,
    long_only: bool = False,
    soft_dd_limit: Decimal = Decimal("15.0"),
    hard_dd_limit: Decimal = Decimal("30.0"),
    cooldown_bars: int = 3,
    taker_fee: Decimal = Decimal("0.0009"),
) -> Tuple[CoreV2Engine, pd.DataFrame]:
    # 1. Load and resample all 18 symbols to 4h
    dfs: Dict[str, pd.DataFrame] = {}
    timestamps_set = set()

    for sym in DEFAULT_SYMBOLS:
        p = DATA_DIR / f"{sym}_1h.parquet"
        if not p.exists():
            continue
        df = pd.read_parquet(p)
        if len(df) < 300:
            continue
        df_4h = resample_and_compute_4h_indicators(df, donchian_window=30, atr_period=14)
        df_4h = df_4h.dropna(subset=["upper", "lower", "ema_slow", "atr", "volume_sma"]).reset_index(drop=True)
        dfs[sym] = df_4h.set_index("timestamp")
        timestamps_set.update(df_4h["timestamp"].tolist())

    sorted_ts = sorted(list(timestamps_set))

    # 2. Instrument rules
    instrument_rules = {}
    for sym in DEFAULT_SYMBOLS:
        instrument_rules[sym] = {
            "minOrderQty": Decimal("0.001") if "BTC" in sym else Decimal("0.01"),
            "qtyStep": Decimal("0.001") if "BTC" in sym else Decimal("0.01"),
            "minNotional": Decimal("5.0"),
            "maxOrderQty": Decimal("1000000"),
        }

    # 3. Engine
    engine = CoreV2Engine(
        initial_capital=Decimal("300.0"),
        instrument_rules=instrument_rules,
        base_risk_pct=base_risk_pct,
        choppy_risk_pct=choppy_risk_pct,
        max_leverage=Decimal("3.5"),
        atr_stop_mult=Decimal("2.5"),
        taker_fee=taker_fee,
        slippage=BASE_SLIPPAGE,
        portfolio_leverage=PORTFOLIO_LEVERAGE,
        drawdown_limit_pct=hard_dd_limit,
        cooldown_bars=cooldown_bars,
        enable_btc_delta_filter=enable_btc_filter,
    )

    equity_records = []

    # 4. Chronological 4h bar-by-bar execution
    for i in range(len(sorted_ts) - 1):
        t = sorted_ts[i]
        next_t = sorted_ts[i + 1]

        bars_dict = {}
        next_open_prices = {}

        for sym, df_sym in dfs.items():
            if t in df_sym.index and next_t in df_sym.index:
                row_curr = df_sym.loc[t]
                row_next = df_sym.loc[next_t]

                bars_dict[sym] = {
                    "open": float(row_curr["open"]),
                    "high": float(row_curr["high"]),
                    "low": float(row_curr["low"]),
                    "close": float(row_curr["close"]),
                    "volume": float(row_curr["volume"]),
                    "volume_sma": float(row_curr["volume_sma"]),
                    "upper": float(row_curr["upper"]),
                    "lower": float(row_curr["lower"]),
                    "ema_fast": float(row_curr["ema_fast"]),
                    "ema_slow": float(row_curr["ema_slow"]),
                    "atr": float(row_curr["atr"]),
                }
                next_open_prices[sym] = Decimal(str(row_next["open"]))

        if not bars_dict:
            continue

        # BTC Trend Filter
        btc_bar = bars_dict.get("BTCUSDT")
        btc_bullish = True
        btc_bearish = True
        if btc_bar:
            c = float(btc_bar["close"])
            ef = float(btc_bar["ema_fast"])
            es = float(btc_bar["ema_slow"])
            btc_bullish = ef > es and c > ef
            btc_bearish = ef < es and c < ef

        # Soft de-risking: check current DD
        curr_marks = {s: Decimal(str(b["close"])) for s, b in bars_dict.items()}
        current_eq = engine.get_equity(curr_marks)
        if engine.peak_equity > Decimal("0"):
            current_dd = (engine.peak_equity - current_eq) / engine.peak_equity * Decimal("100")
        else:
            current_dd = Decimal("0")

        # Soft scaling: if DD >= soft_dd_limit, cut risk in half
        if current_dd >= soft_dd_limit:
            engine.risk_manager.state.base_risk_pct = base_risk_pct * Decimal("0.5")
            engine.risk_manager.state.choppy_risk_pct = choppy_risk_pct * Decimal("0.5")
        else:
            engine.risk_manager.state.base_risk_pct = base_risk_pct
            engine.risk_manager.state.choppy_risk_pct = choppy_risk_pct

        # If long_only, suppress short candidates
        if long_only:
            # We can override scanner's max_concurrent_positions for shorts to 0
            engine.scanner.max_concurrent_positions = 2

        # Filter out short candidates if long_only
        bars_for_event = bars_dict.copy()
        if long_only:
            # Setting lower channel to -inf prevents short breakouts
            for s, b in bars_for_event.items():
                b["lower"] = -1e9

        # Process Bar Event
        engine.process_bar_event(
            timestamp=t,
            bars=bars_for_event,
            next_open_prices=next_open_prices,
            manage_positions=True,
            btc_bullish=btc_bullish,
            btc_bearish=btc_bearish,
        )

        # 8-hour funding deduction
        dt = pd.to_datetime(t, unit="ms")
        if dt.hour in (0, 8, 16):
            funding_rates = {s: Decimal("0.0001") for s in engine.open_positions}
            current_prices = {s: Decimal(str(b["close"])) for s, b in bars_dict.items()}
            engine.process_funding_event(t, funding_rates, current_prices)

        # Record equity
        eq = float(engine.get_equity(curr_marks))
        equity_records.append({"timestamp": t, "datetime": dt, "equity": eq})

    eq_df = pd.DataFrame(equity_records)
    return engine, eq_df


def compute_metrics(engine: CoreV2Engine, eq_df: pd.DataFrame) -> Dict:
    trades = engine.closed_trades
    total_trades = len(trades)
    if total_trades == 0:
        return {"total_trades": 0, "final_equity": 300.0, "net_profit": 0.0, "net_return_pct": 0.0,
                "win_rate": 0.0, "profit_factor": 0.0, "max_drawdown": 0.0, "sharpe_ratio": 0.0,
                "pyramided_count": 0, "pyramided_win_rate": 0.0, "trading_halted": engine.trading_halted}

    wins = [t for t in trades if t.net_pnl > Decimal("0")]
    losses = [t for t in trades if t.net_pnl <= Decimal("0")]

    win_rate = (len(wins) / total_trades) * 100.0
    tot_win = sum(t.net_pnl for t in wins)
    tot_loss = abs(sum(t.net_pnl for t in losses))
    pf = float(tot_win / tot_loss) if tot_loss > 0 else 999.0

    final_equity = float(eq_df["equity"].iloc[-1]) if len(eq_df) > 0 else float(engine.realized_equity)
    net_profit = final_equity - 300.0
    net_return_pct = (net_profit / 300.0) * 100.0

    eq_series = eq_df["equity"]
    peak = eq_series.cummax()
    dd = (peak - eq_series) / peak * 100.0
    max_dd = float(dd.max())

    # Annualized Sharpe (4h bars -> 2190 bars/year)
    returns = eq_series.pct_change().dropna()
    mean_ret = returns.mean()
    std_ret = returns.std()
    sharpe = float((mean_ret / std_ret) * np.sqrt(2190)) if std_ret > 0 else 0.0

    pyramided_trades = [t for t in trades if t.was_pyramided]
    pyramid_win_rate = (len([t for t in pyramided_trades if t.net_pnl > 0]) / len(pyramided_trades) * 100.0) if pyramided_trades else 0.0

    # Annual breakdown
    annual_stats = {}
    eq_df["year"] = eq_df["datetime"].dt.year
    for yr in [2024, 2025, 2026]:
        yr_df = eq_df[eq_df["year"] == yr]
        if len(yr_df) > 1:
            y_start = yr_df["equity"].iloc[0]
            y_end = yr_df["equity"].iloc[-1]
            y_ret = ((y_end - y_start) / y_start) * 100.0
            annual_stats[yr] = y_ret
        else:
            annual_stats[yr] = 0.0

    return {
        "final_equity": final_equity,
        "net_profit": net_profit,
        "net_return_pct": net_return_pct,
        "total_trades": total_trades,
        "win_rate": win_rate,
        "profit_factor": pf,
        "max_drawdown": max_dd,
        "sharpe_ratio": sharpe,
        "pyramided_count": len(pyramided_trades),
        "pyramided_win_rate": pyramid_win_rate,
        "trading_halted": engine.trading_halted,
        "annual_returns": annual_stats,
    }


def main():
    print("=" * 80)
    print(" BYBIT 2024-2026 ZERO-LOOKAHEAD 4H AUDIT (I1 / I7 ENHANCEMENTS)")
    print("=" * 80)

    start_time = time.time()

    # 1. 4h Standard (I1): 1.0% Risk, Soft DD 15%, Hard DD 30%, Both Long & Short
    print("\nRunning Run 1: 4h Standard (I1: 1.0% Risk, Both Long & Short)...")
    eng_i1, eq_i1 = run_4h_simulation(
        base_risk_pct=Decimal("0.01"),
        choppy_risk_pct=Decimal("0.003"),
        enable_btc_filter=True,
        long_only=False,
        soft_dd_limit=Decimal("15.0"),
        hard_dd_limit=Decimal("30.0"),
    )
    m_i1 = compute_metrics(eng_i1, eq_i1)

    # 2. 4h Long-Only (I1 + I2 bias): 1.0% Risk, Altcoin Long-Only
    print("Running Run 2: 4h Long-Only Bias (1.0% Risk, Soft DD 15%, Hard DD 30%)...")
    eng_long, eq_long = run_4h_simulation(
        base_risk_pct=Decimal("0.01"),
        choppy_risk_pct=Decimal("0.003"),
        enable_btc_filter=True,
        long_only=True,
        soft_dd_limit=Decimal("15.0"),
        hard_dd_limit=Decimal("30.0"),
    )
    m_long = compute_metrics(eng_long, eq_long)

    # 3. 4h Long-Only with 2.0% Risk
    print("Running Run 3: 4h Long-Only with 2.0% Risk...")
    eng_2pct, eq_2pct = run_4h_simulation(
        base_risk_pct=Decimal("0.02"),
        choppy_risk_pct=Decimal("0.006"),
        enable_btc_filter=True,
        long_only=True,
        soft_dd_limit=Decimal("15.0"),
        hard_dd_limit=Decimal("35.0"),
    )
    m_2pct = compute_metrics(eng_2pct, eq_2pct)

    elapsed = time.time() - start_time
    print(f"\nAudit completed in {elapsed:.1f} seconds.\n")

    print(f"{'Metric':<30} | {'4h Standard (I1, 1%)':<22} | {'4h Long-Only (1%)':<22} | {'4h Long-Only (2%)':<22}")
    print("-" * 105)
    print(f"{'Final Equity ($)':<30} | ${m_i1['final_equity']:<21.2f} | ${m_long['final_equity']:<21.2f} | ${m_2pct['final_equity']:<21.2f}")
    print(f"{'Net Profit ($)':<30} | ${m_i1['net_profit']:<+21.2f} | ${m_long['net_profit']:<+21.2f} | ${m_2pct['net_profit']:<+21.2f}")
    print(f"{'Net Return (%)':<30} | {m_i1['net_return_pct']:<+21.2f}% | {m_long['net_return_pct']:<+21.2f}% | {m_2pct['net_return_pct']:<+21.2f}%")
    print(f"{'Win Rate (%)':<30} | {m_i1['win_rate']:<21.2f}% | {m_long['win_rate']:<21.2f}% | {m_2pct['win_rate']:<21.2f}%")
    print(f"{'Profit Factor':<30} | {m_i1['profit_factor']:<21.2f} | {m_long['profit_factor']:<21.2f} | {m_2pct['profit_factor']:<21.2f}")
    print(f"{'Max Drawdown (%)':<30} | {m_i1['max_drawdown']:<21.2f}% | {m_long['max_drawdown']:<21.2f}% | {m_2pct['max_drawdown']:<21.2f}%")
    print(f"{'Sharpe Ratio':<30} | {m_i1['sharpe_ratio']:<21.2f} | {m_long['sharpe_ratio']:<21.2f} | {m_2pct['sharpe_ratio']:<21.2f}")
    print(f"{'Total Trades':<30} | {m_i1['total_trades']:<21} | {m_long['total_trades']:<21} | {m_2pct['total_trades']:<21}")
    print(f"{'Pyramided Trades':<30} | {m_i1['pyramided_count']:<21} | {m_long['pyramided_count']:<21} | {m_2pct['pyramided_count']:<21}")
    print(f"{'Pyramid Win Rate':<30} | {m_i1['pyramided_win_rate']:<21.2f}% | {m_long['pyramided_win_rate']:<21.2f}% | {m_2pct['pyramided_win_rate']:<21.2f}%")
    print(f"{'Circuit Breaker':<30} | {'HALTED' if m_i1['trading_halted'] else 'NORMAL':<21} | {'HALTED' if m_long['trading_halted'] else 'NORMAL':<21} | {'HALTED' if m_2pct['trading_halted'] else 'NORMAL':<21}")
    print(f"{'2024 Return':<30} | {m_i1['annual_returns'].get(2024, 0):<+21.2f}% | {m_long['annual_returns'].get(2024, 0):<+21.2f}% | {m_2pct['annual_returns'].get(2024, 0):<+21.2f}%")
    print(f"{'2025 Return':<30} | {m_i1['annual_returns'].get(2025, 0):<+21.2f}% | {m_long['annual_returns'].get(2025, 0):<+21.2f}% | {m_2pct['annual_returns'].get(2025, 0):<+21.2f}%")
    print(f"{'2026 Return':<30} | {m_i1['annual_returns'].get(2026, 0):<+21.2f}% | {m_long['annual_returns'].get(2026, 0):<+21.2f}% | {m_2pct['annual_returns'].get(2026, 0):<+21.2f}%")


if __name__ == "__main__":
    main()
