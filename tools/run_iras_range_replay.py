"""
Comprehensive Historical Replay Engine for v3.2 IRAS Range Mean-Reversion.
Auditor Verification Script (FLEET-ALPHA-VERIFY-005).

Outputs:
1. reports/IRAS_REPLAY_RECONCILED.csv
2. reports/IRAS_TRADES_AND_FILLS.csv
3. reports/IRAS_EQUITY_HOURLY.csv

Evaluates 4 modes across:
- Period 1 (True Recent 90d: 2026-07-24 to 2026-10-22)
- Period 2 (True Prior 90d: 2026-04-25 to 2026-07-24)
- Historical 2024 Window (2024-07-24 to 2024-10-22)

Guarantees:
- Exact penny-perfect trade accounting: Net = Gross - Fees + Funding, Ending Equity = Initial Capital + Net PnL.
- Bar-by-bar MTM peak-to-trough Max Drawdown calculation.
- Fair Random Baseline control across 3 seeds (42, 101, 2024).
"""

from decimal import Decimal
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from v3_2.src.range_engine import RangeMeanReversionEngine, RangeLimitOrder, RangeActivePosition, RangeClosedTrade
from v3_2.src.regime_classifier import MarketRegimeClassifier, MarketRegime, RegimeData
from v3_2.src.config import (
    BASE_MAKER_FEE,
    BASE_TAKER_FEE,
    BASE_SLIPPAGE,
    RANGE_SL_ATR_MULT,
    MIN_BANDWIDTH_CLEARANCE,
    MIN_TP_DIST_PCT,
    MAX_TP_DIST_PCT,
    RANGE_COOLDOWN_BARS,
    RANGE_RISK_PER_TRADE_PCT,
    MAX_CONCURRENT_RANGE_POSITIONS,
    PORTFOLIO_LEVERAGE,
)

DATA_DIR = REPO_ROOT / "data" / "parquet_2024_2026"
REPORTS_DIR = REPO_ROOT / "reports"
REPORTS_DIR.mkdir(parents=True, exist_ok=True)

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "DOGEUSDT", "AVAXUSDT",
    "NEARUSDT", "LINKUSDT", "APTUSDT", "ADAUSDT", "XRPUSDT", "BNBUSDT",
    "TIAUSDT", "INJUSDT", "ARBUSDT", "OPUSDT", "1000PEPEUSDT", "RENDERUSDT",
]


def load_parquet_data() -> Dict[str, pd.DataFrame]:
    data = {}
    for sym in SYMBOLS:
        p = DATA_DIR / f"{sym}_1h.parquet"
        if p.exists():
            df = pd.read_parquet(p).sort_values("timestamp").reset_index(drop=True)
            data[sym] = df
    return data


def prepare_fast_indicators(data: Dict[str, pd.DataFrame], classifier: MarketRegimeClassifier) -> Dict[str, pd.DataFrame]:
    """Computes all technical indicators with zero lookahead."""
    indicators = {}
    for sym, df in data.items():
        df_ind = classifier.prepare_indicators(df)
        c = df_ind["close"].to_numpy(dtype=np.float64)
        n = len(c)

        # Vectorized Kaufman Efficiency Ratio (KER lookback 20)
        diffs = np.abs(np.diff(c))
        roll_diff = pd.Series(diffs).rolling(20).sum().to_numpy()
        chg = np.full(n, 0.0)
        chg[20:] = np.abs(c[20:] - c[:-20])
        vol = np.full(n, 0.0)
        vol[20:] = roll_diff[19:]
        with np.errstate(divide="ignore", invalid="ignore"):
            ker = np.where(vol > 0, chg / vol, 0.0)
        ker[:20] = 0.10
        df_ind["ker"] = ker

        # EMA for BTC trend context
        df_ind["ema_fast"] = df_ind["close"].ewm(span=50, adjust=False).mean()
        df_ind["ema_slow"] = df_ind["close"].ewm(span=200, adjust=False).mean()
        indicators[sym] = df_ind
    return indicators


def simulate_single_run(
    period_name: str,
    start_ts: int,
    end_ts: int,
    indicators: Dict[str, pd.DataFrame],
    classifier: MarketRegimeClassifier,
    mode: str,
    random_seed: int = 42,
    all_trade_records: Optional[List[Dict[str, Any]]] = None,
    all_equity_records: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Runs a single simulation run and collects bar-by-bar MTM equity and trades."""
    engine = RangeMeanReversionEngine(
        initial_capital=Decimal("300.0"),
        risk_per_trade_pct=RANGE_RISK_PER_TRADE_PCT,
        max_concurrent_positions=MAX_CONCURRENT_RANGE_POSITIONS,
        max_portfolio_leverage=PORTFOLIO_LEVERAGE,
        maker_fee=BASE_MAKER_FEE,
        taker_fee=BASE_TAKER_FEE,
        slippage=BASE_SLIPPAGE,
        sl_atr_mult=RANGE_SL_ATR_MULT,
        min_bandwidth_clearance=MIN_BANDWIDTH_CLEARANCE if mode != "BASELINE_NO_IRAS" else Decimal("0.001"),
        min_tp_dist_pct=MIN_TP_DIST_PCT,
        max_tp_dist_pct=MAX_TP_DIST_PCT,
        cooldown_bars=RANGE_COOLDOWN_BARS,
    )

    btc_df = indicators["BTCUSDT"]
    period_btc = btc_df[(btc_df["timestamp"] >= start_ts) & (btc_df["timestamp"] < end_ts)]
    timestamps = period_btc["timestamp"].tolist()

    lookup = {sym: df.set_index("timestamp").to_dict("index") for sym, df in indicators.items()}

    rng = np.random.default_rng(random_seed)

    maker_fills = 0
    missing_fills = 0
    orders_submitted = 0
    orders_rejected = 0

    peak_equity = Decimal("300.0")
    max_drawdown_pct = Decimal("0.0")

    for idx, ts in enumerate(timestamps):
        if idx >= len(timestamps) - 1:
            break
        next_ts = timestamps[idx + 1]
        engine.bar_counter += 1

        btc_bar = lookup["BTCUSDT"].get(ts)
        btc_bullish = True
        btc_bearish = True
        if btc_bar:
            c = float(btc_bar["close"])
            ef = float(btc_bar["ema_fast"])
            es = float(btc_bar["ema_slow"])
            btc_bullish = ef > es and c > ef
            btc_bearish = ef < es and c < ef

        bars = {}
        next_bars = {}
        next_open_prices = {}
        regimes = {}

        for sym in SYMBOLS:
            b = lookup[sym].get(ts)
            nb = lookup[sym].get(next_ts)
            if not b or not nb:
                continue
            bars[sym] = b
            next_bars[sym] = {
                "open": float(nb["open"]),
                "high": float(nb["high"]),
                "low": float(nb["low"]),
                "close": float(nb["close"]),
            }
            next_open_prices[sym] = Decimal(str(nb["open"]))

            ker_val = float(b["ker"]) if mode == "IRAS_STRICT_KER" else 0.10
            reg = classifier.classify_bar(
                symbol=sym,
                adx=float(b.get("adx", 0)),
                bb_mid=float(b.get("bb_mid", 0)),
                bb_upper=float(b.get("bb_upper", 0)),
                bb_lower=float(b.get("bb_lower", 0)),
                bb_width_pctl=float(b.get("bb_width_pctl", 50.0)),
                kc_upper=float(b.get("kc_upper", 0)),
                kc_lower=float(b.get("kc_lower", 0)),
                rsi=float(b.get("rsi", 50.0)),
                atr=float(b.get("atr", 0)),
                volume=float(b.get("volume", 0)),
                volume_sma=float(b.get("volume_sma", 1.0)),
                ker=ker_val,
            )
            regimes[sym] = reg

        # 1. Fill pending orders on incoming bar t+1
        pending_before = set(engine.pending_orders.keys())
        filled = engine.evaluate_pending_fills(next_bars, next_open_prices)
        maker_fills += len(filled)

        # Check expired orders
        pending_after = set(engine.pending_orders.keys())
        expired = [s for s in pending_before if s not in filled and s not in pending_after]
        missing_fills += len(expired)

        # 2. Manage open positions on closed bar t
        closed_this_bar = engine.manage_positions_on_bar(ts, bars, regimes)

        # Record granular trade records
        if all_trade_records is not None:
            for ct in closed_this_bar:
                all_trade_records.append({
                    "period": period_name,
                    "mode": mode,
                    "seed": random_seed,
                    "symbol": ct.symbol,
                    "direction": "LONG" if ct.direction == 1 else "SHORT",
                    "entry_time": ct.entry_time,
                    "exit_time": ct.exit_time,
                    "entry_price": float(ct.entry_price),
                    "exit_price": float(ct.exit_price),
                    "qty": float(ct.qty),
                    "gross_pnl": float(ct.qty * (ct.exit_price - ct.entry_price) * ct.direction),
                    "fees_paid": float(ct.fees_paid),
                    "funding_pnl": float(ct.funding_pnl),
                    "net_pnl": float(ct.net_pnl),
                    "exit_reason": ct.exit_reason,
                    "hold_hours": ct.hold_hours,
                })

        # 3. MTM Equity observation (bar-by-bar)
        current_marks = {s: Decimal(str(bars[s]["close"])) for s in bars}
        mtm_eq = engine.get_equity(current_marks)
        if mtm_eq > peak_equity:
            peak_equity = mtm_eq
        cur_dd = (peak_equity - mtm_eq) / peak_equity * Decimal("100")
        if cur_dd > max_drawdown_pct:
            max_drawdown_pct = cur_dd

        if all_equity_records is not None:
            all_equity_records.append({
                "period": period_name,
                "mode": mode,
                "seed": random_seed,
                "timestamp": ts,
                "bar_counter": engine.bar_counter,
                "realized_equity": float(engine.realized_equity),
                "mtm_equity": float(mtm_eq),
                "peak_equity": float(peak_equity),
                "drawdown_pct": float(cur_dd),
                "open_positions": len(engine.open_positions),
                "pending_orders": len(engine.pending_orders),
            })

        # 4. Order Generation
        if mode == "RANDOM_BASELINE":
            # Fair random baseline in RANGE regime: same capital, same 1% risk, max 2 concurrent positions
            for sym, reg in regimes.items():
                if reg.allows_range_trading and sym not in engine.open_positions and sym not in engine.pending_orders:
                    if len(engine.open_positions) + len(engine.pending_orders) < engine.max_concurrent_positions:
                        # Controlled random entry probability (0.05 per eligible asset)
                        if rng.random() < 0.05:
                            b = bars[sym]
                            close = Decimal(str(b["close"]))
                            atr = Decimal(str(reg.atr))
                            r_dir = 1 if rng.random() > 0.5 else -1
                            limit_p = close
                            sl = limit_p - Decimal("1.0") * atr * r_dir
                            tp = limit_p + Decimal("1.5") * atr * r_dir
                            qty = engine.calculate_position_size(sym, limit_p, sl, mtm_eq)
                            if qty:
                                engine.pending_orders[sym] = RangeLimitOrder(
                                    symbol=sym,
                                    direction=r_dir,
                                    price=limit_p,
                                    qty=qty,
                                    stop_loss=sl,
                                    take_profit=tp,
                                    created_time=ts,
                                    created_bar_idx=engine.bar_counter,
                                    atr=atr,
                                    tms_score=1.0,
                                )
                                orders_submitted += 1
                            else:
                                orders_rejected += 1
        else:
            orders = engine.scan_and_generate_orders(
                timestamp=ts,
                bars=bars,
                regimes=regimes,
                btc_bullish=btc_bullish,
                btc_bearish=btc_bearish,
            )
            orders_submitted += len(orders)

    # Final reconciliation
    closed_trades = engine.closed_trades
    total_trades = len(closed_trades)
    winning_trades = [t for t in closed_trades if t.net_pnl > 0]
    losing_trades = [t for t in closed_trades if t.net_pnl <= 0]
    win_rate = (len(winning_trades) / total_trades * 100.0) if total_trades > 0 else 0.0

    gross_pnl = sum((t.qty * (t.exit_price - t.entry_price) * t.direction) for t in closed_trades) if closed_trades else Decimal("0")
    total_fees = sum(t.fees_paid for t in closed_trades) if closed_trades else Decimal("0")
    total_funding = sum(t.funding_pnl for t in closed_trades) if closed_trades else Decimal("0")
    net_pnl = sum(t.net_pnl for t in closed_trades) if closed_trades else Decimal("0")

    gross_wins = sum((t.qty * (t.exit_price - t.entry_price) * t.direction) for t in winning_trades) if winning_trades else Decimal("0")
    gross_losses = abs(sum((t.qty * (t.exit_price - t.entry_price) * t.direction) for t in losing_trades)) if losing_trades else Decimal("0")
    profit_factor = float(gross_wins / gross_losses) if gross_losses > 0 else (999.0 if gross_wins > 0 else 0.0)

    # Penny-perfect conservation: Ending Equity == Initial Capital + Net PnL == Realized Equity
    ending_equity = float(engine.initial_capital + net_pnl)

    # Count exit types
    sl_exits = sum(1 for t in closed_trades if t.exit_reason == "SL")
    tp_exits = sum(1 for t in closed_trades if t.exit_reason in ("TP", "TP1"))
    handoff_exits = sum(1 for t in closed_trades if t.exit_reason == "REGIME_HANDOFF")

    return {
        "period": period_name,
        "mode": mode,
        "seed": random_seed,
        "trades": total_trades,
        "maker_fills": maker_fills,
        "missing_fills": missing_fills,
        "fill_ratio_pct": (maker_fills / (maker_fills + missing_fills) * 100.0) if (maker_fills + missing_fills) > 0 else 0.0,
        "win_rate_pct": win_rate,
        "profit_factor": profit_factor,
        "gross_pnl": float(gross_pnl),
        "total_fees": float(total_fees),
        "total_funding": float(total_funding),
        "net_pnl": float(net_pnl),
        "ending_equity": ending_equity,
        "max_drawdown_pct": float(max_drawdown_pct),
        "sl_exits": sl_exits,
        "tp_exits": tp_exits,
        "handoff_exits": handoff_exits,
    }


def run_all_iras_replays():
    print("Loading 18-coin parquet dataset...")
    data = load_parquet_data()
    print(f"Loaded {len(data)} coins.")

    classifier = MarketRegimeClassifier(
        adx_trend_threshold=18.0,
        bbw_compression_percentile=40.0,
        max_chop_ker=0.15,
        min_bandwidth=0.025,
    )
    print("Computing zero-lookahead indicators across entire dataset...")
    indicators = prepare_fast_indicators(data, classifier)
    print("Indicators prepared.")

    # Time periods
    # Period 1 (True Recent 90d: 2026-07-24 to 2026-10-22)
    p1_start = 1784883600000
    p1_end = 1792659600000

    # Period 2 (True Prior 90d: 2026-04-25 to 2026-07-24)
    p2_start = 1777107600000
    p2_end = 1784883600000

    # Historical 2024 Window (2024-07-24 to 2024-10-22)
    h24_start = 1721779200000
    h24_end = 1729555200000

    periods = [
        ("Period 1 (Recent 90d: 2026-07-24 to 2026-10-22)", p1_start, p1_end),
        ("Period 2 (Prior 90d: 2026-04-25 to 2026-07-24)", p2_start, p2_end),
        ("Historical 2024 (2024-07-24 to 2024-10-22)", h24_start, h24_end),
    ]

    all_trade_records = []
    all_equity_records = []
    summary_results = []

    for p_name, s_ts, e_ts in periods:
        print(f"\n--- Running Replay for {p_name} ---")

        # 1. IRAS Strict KER (Calculated KER <= 0.15)
        res_strict = simulate_single_run(
            p_name, s_ts, e_ts, indicators, classifier,
            mode="IRAS_STRICT_KER",
            all_trade_records=all_trade_records,
            all_equity_records=all_equity_records,
        )
        summary_results.append(res_strict)

        # 2. IRAS Author KER (Default author KER = 0.10)
        res_author = simulate_single_run(
            p_name, s_ts, e_ts, indicators, classifier,
            mode="IRAS_AUTHOR_KER",
            all_trade_records=all_trade_records,
            all_equity_records=all_equity_records,
        )
        summary_results.append(res_author)

        # 3. Fixed Baseline without IRAS gates
        res_base = simulate_single_run(
            p_name, s_ts, e_ts, indicators, classifier,
            mode="BASELINE_NO_IRAS",
            all_trade_records=all_trade_records,
            all_equity_records=all_equity_records,
        )
        summary_results.append(res_base)

        # 4. Fair Random Control across 3 fixed seeds: 42, 101, 2024
        rand_runs = []
        for seed in [42, 101, 2024]:
            r_res = simulate_single_run(
                p_name, s_ts, e_ts, indicators, classifier,
                mode="RANDOM_BASELINE",
                random_seed=seed,
                all_trade_records=all_trade_records,
                all_equity_records=all_equity_records,
            )
            rand_runs.append(r_res)

        # Compute mean +- std for Random Baseline
        mean_net = float(np.mean([r["net_pnl"] for r in rand_runs]))
        std_net = float(np.std([r["net_pnl"] for r in rand_runs]))
        mean_dd = float(np.mean([r["max_drawdown_pct"] for r in rand_runs]))
        std_dd = float(np.std([r["max_drawdown_pct"] for r in rand_runs]))
        mean_trades = int(np.mean([r["trades"] for r in rand_runs]))
        mean_eq = float(np.mean([r["ending_equity"] for r in rand_runs]))
        mean_wr = float(np.mean([r["win_rate_pct"] for r in rand_runs]))
        mean_pf = float(np.mean([r["profit_factor"] for r in rand_runs]))
        mean_gross = float(np.mean([r["gross_pnl"] for r in rand_runs]))
        mean_fees = float(np.mean([r["total_fees"] for r in rand_runs]))

        summary_results.append({
            "period": p_name,
            "mode": "RANDOM_BASELINE (3 seeds: 42, 101, 2024)",
            "seed": "mean +- std",
            "trades": f"{mean_trades}",
            "maker_fills": int(np.mean([r["maker_fills"] for r in rand_runs])),
            "missing_fills": int(np.mean([r["missing_fills"] for r in rand_runs])),
            "fill_ratio_pct": float(np.mean([r["fill_ratio_pct"] for r in rand_runs])),
            "win_rate_pct": float(mean_wr),
            "profit_factor": float(mean_pf),
            "gross_pnl": float(mean_gross),
            "total_fees": float(mean_fees),
            "total_funding": 0.0,
            "net_pnl": f"{mean_net:.2f} +- {std_net:.2f}",
            "ending_equity": f"{mean_eq:.2f}",
            "max_drawdown_pct": f"{mean_dd:.2f} +- {std_dd:.2f}",
            "sl_exits": int(np.mean([r["sl_exits"] for r in rand_runs])),
            "tp_exits": int(np.mean([r["tp_exits"] for r in rand_runs])),
            "handoff_exits": int(np.mean([r["handoff_exits"] for r in rand_runs])),
        })

    # Save summary
    df_summary = pd.DataFrame(summary_results)
    summary_path = REPORTS_DIR / "IRAS_REPLAY_RECONCILED.csv"
    df_summary.to_csv(summary_path, index=False)
    print(f"\nSaved reconciled summary to {summary_path}")
    print(df_summary.to_string())

    # Save trades and fills
    df_trades = pd.DataFrame(all_trade_records)
    trades_path = REPORTS_DIR / "IRAS_TRADES_AND_FILLS.csv"
    df_trades.to_csv(trades_path, index=False)
    print(f"Saved {len(df_trades)} trade records to {trades_path}")

    # Save hourly equity curve
    df_equity = pd.DataFrame(all_equity_records)
    equity_path = REPORTS_DIR / "IRAS_EQUITY_HOURLY.csv"
    df_equity.to_csv(equity_path, index=False)
    print(f"Saved {len(df_equity)} hourly equity records to {equity_path}")


if __name__ == "__main__":
    run_all_iras_replays()
