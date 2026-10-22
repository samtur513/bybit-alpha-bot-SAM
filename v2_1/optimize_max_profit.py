"""
Quantitative Profit Maximizer for Multi-Alpha Growth Engine v2.1.
Systematically tests parameter grids on Bybit 2024-2026 offline data (18 coins, 6,123 4h bars).
Finds the mathematically optimal configuration for MAXIMUM PROFIT while keeping Max Drawdown <= 30%.
"""

from decimal import Decimal
import itertools
import logging
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Tuple
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2_1.src.config import (
    DEFAULT_SYMBOLS,
    BASE_TAKER_FEE,
    BASE_SLIPPAGE,
    PORTFOLIO_LEVERAGE,
)
from v2_1.src.core_v2 import CoreV2Engine
from v2_1.src.regime_classifier import MarketRegimeClassifier

logging.basicConfig(level=logging.WARNING)

DATA_DIR = REPO_ROOT / "data" / "parquet_2024_2026"


def prepare_multi_window_data(symbols: List[str], donchian_windows: List[int]) -> Tuple[Dict[str, Any], List[int]]:
    """Loads 18 symbols and pre-computes indicators for multiple Donchian lookback windows."""
    classifier = MarketRegimeClassifier()
    universe = {}
    timestamps_set = set()

    for sym in symbols:
        p = DATA_DIR / f"{sym}_1h.parquet"
        if not p.exists():
            continue
        df_1h = pd.read_parquet(p).sort_values("timestamp").reset_index(drop=True)
        df_1h["dt"] = pd.to_datetime(df_1h["timestamp"], unit="ms")

        # 4h Resample
        df_4h = (
            df_1h.resample("4h", on="dt")
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

        # Multiple Donchian windows with causal shift(1)
        for w in donchian_windows:
            df_4h[f"upper_{w}"] = high.shift(1).rolling(w).max()
            df_4h[f"lower_{w}"] = low.shift(1).rolling(w).min()

        # Wilder ATR(14)
        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df_4h["atr"] = tr.ewm(alpha=1.0 / 14, adjust=False).mean()

        df_4h["ema_fast"] = close.ewm(span=50, adjust=False).mean()
        df_4h["ema_slow"] = close.ewm(span=200, adjust=False).mean()
        df_4h["volume_sma"] = volume.rolling(20).mean()

        df_4h = classifier.prepare_indicators(df_4h)
        df_4h = df_4h.dropna(subset=[f"upper_{w}" for w in donchian_windows] + ["ema_slow", "atr", "volume_sma", "adx"]).reset_index(drop=True)

        universe[sym] = {
            "open": df_4h.set_index("timestamp")["open"].to_dict(),
            "high": df_4h.set_index("timestamp")["high"].to_dict(),
            "low": df_4h.set_index("timestamp")["low"].to_dict(),
            "close": df_4h.set_index("timestamp")["close"].to_dict(),
            "volume": df_4h.set_index("timestamp")["volume"].to_dict(),
            "volume_sma": df_4h.set_index("timestamp")["volume_sma"].to_dict(),
            "ema_fast": df_4h.set_index("timestamp")["ema_fast"].to_dict(),
            "ema_slow": df_4h.set_index("timestamp")["ema_slow"].to_dict(),
            "atr": df_4h.set_index("timestamp")["atr"].to_dict(),
            "adx": df_4h.set_index("timestamp")["adx"].to_dict(),
        }
        for w in donchian_windows:
            universe[sym][f"upper_{w}"] = df_4h.set_index("timestamp")[f"upper_{w}"].to_dict()
            universe[sym][f"lower_{w}"] = df_4h.set_index("timestamp")[f"lower_{w}"].to_dict()

        timestamps_set.update(df_4h["timestamp"].tolist())

    sorted_ts = sorted(list(timestamps_set))
    return universe, sorted_ts


def simulate_candidate(
    universe: Dict[str, Any],
    sorted_ts: List[int],
    donchian_w: int,
    base_risk: Decimal,
    min_adx: float,
    pyramid_trigger: Decimal,
    pyramid_ratio: Decimal,
    trailing_mult: Decimal,
    long_only: bool,
    soft_dd: Decimal = Decimal("15.0"),
    hard_dd: Decimal = Decimal("30.0"),
) -> Dict[str, Any]:
    instrument_rules = {
        s: {
            "minOrderQty": Decimal("0.001") if "BTC" in s else Decimal("0.01"),
            "qtyStep": Decimal("0.001") if "BTC" in s else Decimal("0.01"),
            "minNotional": Decimal("5.0"),
            "maxOrderQty": Decimal("1000000"),
        }
        for s in universe
    }

    engine = CoreV2Engine(
        initial_capital=Decimal("300.0"),
        instrument_rules=instrument_rules,
        base_risk_pct=base_risk,
        choppy_risk_pct=base_risk * Decimal("0.3"),
        max_leverage=Decimal("3.5"),
        atr_stop_mult=trailing_mult,
        taker_fee=BASE_TAKER_FEE,
        slippage=BASE_SLIPPAGE,
        portfolio_leverage=PORTFOLIO_LEVERAGE,
        drawdown_limit_pct=hard_dd,
        min_adx=min_adx,
        cooldown_bars=3,
        enable_btc_delta_filter=True,
    )
    # Custom pyramiding parameters
    engine.pyramiding_engine.pyramid_trigger_atr_mult = pyramid_trigger
    engine.pyramiding_engine.pyramid_add_ratio = pyramid_ratio
    engine.pyramiding_engine.trailing_atr_mult = trailing_mult

    equity_history = []

    for i in range(len(sorted_ts) - 1):
        t = sorted_ts[i]
        next_t = sorted_ts[i + 1]

        bars_dict = {}
        next_open_prices = {}

        for sym, fd in universe.items():
            if t in fd["close"] and next_t in fd["close"]:
                b_curr = {
                    "open": fd["open"][t],
                    "high": fd["high"][t],
                    "low": fd["low"][t],
                    "close": fd["close"][t],
                    "volume": fd["volume"][t],
                    "volume_sma": fd["volume_sma"][t],
                    "upper": fd[f"upper_{donchian_w}"][t],
                    "lower": fd[f"lower_{donchian_w}"][t] if not long_only else -1e9,
                    "ema_fast": fd["ema_fast"][t],
                    "ema_slow": fd["ema_slow"][t],
                    "atr": fd["atr"][t],
                    "adx": fd["adx"][t],
                }
                bars_dict[sym] = b_curr
                next_open_prices[sym] = Decimal(str(fd["open"][next_t]))

        if not bars_dict:
            continue

        btc_bar = bars_dict.get("BTCUSDT")
        btc_bullish = True
        btc_bearish = True
        if btc_bar:
            c = float(btc_bar["close"])
            ef = float(btc_bar["ema_fast"])
            es = float(btc_bar["ema_slow"])
            btc_bullish = ef > es and c > ef
            btc_bearish = ef < es and c < ef

        curr_marks = {s: Decimal(str(b["close"])) for s, b in bars_dict.items()}
        current_eq = engine.get_equity(curr_marks)
        if engine.peak_equity > Decimal("0"):
            current_dd = (engine.peak_equity - current_eq) / engine.peak_equity * Decimal("100")
        else:
            current_dd = Decimal("0")

        # Soft de-risking
        if current_dd >= soft_dd:
            engine.risk_manager.state.base_risk_pct = base_risk * Decimal("0.5")
        else:
            engine.risk_manager.state.base_risk_pct = base_risk

        engine.process_bar_event(
            timestamp=t,
            bars=bars_dict,
            next_open_prices=next_open_prices,
            manage_positions=True,
            btc_bullish=btc_bullish,
            btc_bearish=btc_bearish,
        )

        dt = pd.to_datetime(t, unit="ms")
        if dt.hour in (0, 8, 16):
            trend_funding = {s: Decimal("0.0001") for s in engine.open_positions}
            engine.process_funding_event(t, trend_funding, curr_marks)

        eq = float(engine.get_equity(curr_marks))
        equity_history.append({"t": t, "dt": dt, "eq": eq})

    eq_df = pd.DataFrame(equity_history)
    trades = engine.closed_trades
    total_trades = len(trades)
    if total_trades == 0:
        return {"profit": 0.0, "final_eq": 300.0, "max_dd": 0.0, "sharpe": 0.0, "trades": 0}

    wins = [t for t in trades if t.net_pnl > 0]
    wr = len(wins) / total_trades * 100.0
    tot_win = sum(t.net_pnl for t in wins)
    tot_loss = abs(sum(t.net_pnl for t in trades if t.net_pnl <= 0))
    pf = float(tot_win / tot_loss) if tot_loss > 0 else 999.0

    final_eq = float(eq_df["eq"].iloc[-1])
    net_profit = final_eq - 300.0

    eq_s = eq_df["eq"]
    peak = eq_s.cummax()
    dd = (peak - eq_s) / peak * 100.0
    max_dd = float(dd.max())

    ret = eq_s.pct_change().dropna()
    sharpe = float((ret.mean() / ret.std()) * np.sqrt(2190)) if ret.std() > 0 else 0.0

    pyr_trades = [t for t in trades if getattr(t, "was_pyramided", False)]
    pyr_wr = (len([t for t in pyr_trades if t.net_pnl > 0]) / len(pyr_trades) * 100.0) if pyr_trades else 0.0

    # 2024, 2025, 2026 returns
    eq_df["year"] = eq_df["dt"].dt.year
    yr_rets = {}
    for y in [2024, 2025, 2026]:
        ydf = eq_df[eq_df["year"] == y]
        yr_rets[y] = ((ydf["eq"].iloc[-1] - ydf["eq"].iloc[0]) / ydf["eq"].iloc[0] * 100.0) if len(ydf) > 1 else 0.0

    return {
        "final_eq": final_eq,
        "net_profit": net_profit,
        "roi_pct": (net_profit / 300.0) * 100.0,
        "max_dd": max_dd,
        "sharpe": sharpe,
        "win_rate": wr,
        "profit_factor": pf,
        "total_trades": total_trades,
        "pyr_trades": len(pyr_trades),
        "pyr_wr": pyr_wr,
        "yr_rets": yr_rets,
        "halted": engine.trading_halted,
    }


def main():
    print("=" * 80)
    print(" RUNNING SYSTEMATIC PROFIT MAXIMIZATION GRID SEARCH")
    print("=" * 80)

    t0 = time.time()
    windows = [25, 30]
    universe, sorted_ts = prepare_multi_window_data(DEFAULT_SYMBOLS, windows)
    print(f"Data prepared in {time.time()-t0:.1f}s.")

    # Grid of candidate parameters
    param_grid = {
        "donchian_w": [25, 30],
        "base_risk": [Decimal("0.015"), Decimal("0.020"), Decimal("0.025")],
        "min_adx": [18.0, 20.0, 22.0],
        "pyramid_trigger": [Decimal("1.5"), Decimal("2.0")],
        "pyramid_ratio": [Decimal("0.5"), Decimal("0.6")],
        "trailing_mult": [Decimal("2.2"), Decimal("2.5")],
        "long_only": [False, True],
    }

    keys, values = zip(*param_grid.items())
    combinations = [dict(zip(keys, v)) for v in itertools.product(*values)]
    print(f"Total combinations to evaluate: {len(combinations)}")

    results = []

    for idx, params in enumerate(combinations):
        res = simulate_candidate(
            universe=universe,
            sorted_ts=sorted_ts,
            donchian_w=params["donchian_w"],
            base_risk=params["base_risk"],
            min_adx=params["min_adx"],
            pyramid_trigger=params["pyramid_trigger"],
            pyramid_ratio=params["pyramid_ratio"],
            trailing_mult=params["trailing_mult"],
            long_only=params["long_only"],
        )
        entry = {**params, **res}
        results.append(entry)
        if (idx + 1) % 50 == 0 or idx == len(combinations) - 1:
            print(f"Evaluated {idx+1}/{len(combinations)} | Best Profit so far: ${max(r['net_profit'] for r in results):.2f}")

    # Filter out combinations that breached Max DD > 30% or halted
    safe_results = [r for r in results if r["max_dd"] <= 30.0 and not r["halted"]]
    safe_results.sort(key=lambda x: x["final_eq"], reverse=True)

    print("\n" + "=" * 90)
    print(" TOP 5 MAXIMUM PROFIT CONFIGURATIONS (MAX DD <= 30% GUARANTEED):")
    print("=" * 90)

    for i, top in enumerate(safe_results[:5]):
        print(f"\nRANK #{i+1}: Final Capital: ${top['final_eq']:.2f} (Profit: +${top['net_profit']:.2f} / +{top['roi_pct']:.1f}% ROI)")
        print(f"  Max Drawdown: {top['max_dd']:.2f}% | Sharpe: {top['sharpe']:.2f} | PF: {top['profit_factor']:.2f} | WR: {top['win_rate']:.1f}%")
        print(f"  Trades: {top['total_trades']} | Pyramid Trades: {top['pyr_trades']} (Win Rate: {top['pyr_wr']:.1f}%)")
        print(f"  Parameters: Donchian: {top['donchian_w']}b | Risk: {top['base_risk']*100}% | ADX >= {top['min_adx']} | Long Only: {top['long_only']}")
        print(f"              Pyramid Trigger: +{top['pyramid_trigger']} ATR | Add: +{top['pyramid_ratio']*100}% | Trailing Stop: {top['trailing_mult']} ATR")
        print(f"  Returns by Year: 2024: {top['yr_rets'].get(2024, 0):+.1f}% | 2025: {top['yr_rets'].get(2025, 0):+.1f}% | 2026: {top['yr_rets'].get(2026, 0):+.1f}%")

    # Save complete table
    df_res = pd.DataFrame(safe_results)
    df_res.to_csv(REPO_ROOT / "reports" / "PROFIT_OPTIMIZATION_GRID_RESULTS.csv", index=False)
    print(f"\nFull grid results saved to reports/PROFIT_OPTIMIZATION_GRID_RESULTS.csv")


if __name__ == "__main__":
    main()
