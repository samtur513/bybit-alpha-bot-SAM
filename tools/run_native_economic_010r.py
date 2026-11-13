"""
tools/run_native_economic_010r.py

Step 5 Execution: Native Economic Test for Gate 010R.
Executes the 3 primary native strategies:
- v3.1.4 Champion 4h
- v3.8 Trend Lab 1h
- v3.2 Dual-Regime IRAS

Evaluates:
- 90-day recent benchmark window (2026-07-24 to 2026-10-22)
- 90-day retrospective benchmark window (2024-07-24 to 2024-10-22)
- In-Sample (2024-01-01 to 2025-06-30)
- Out-of-Sample (2025-07-01 to 2026-10-22)
- Full period (2024-01-01 to 2026-10-22)
Across OPT, BASE, and STRESS scenarios.

Generates:
- reports/NATIVE_ECONOMIC_010R_SCOREBOARD.csv
- reports/NATIVE_ECONOMIC_010R_TRADES.csv
- reports/NATIVE_ECONOMIC_010R_EQUITY.csv
- reports/NATIVE_ECONOMIC_010R_COSTS.csv
"""

from decimal import Decimal
from pathlib import Path
import sys
import time
from typing import List, Dict, Any
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.native_economic_010r_runner import (
    NativeEconomicRunner,
    ScenarioConfig,
    CONFIG_OPT,
    CONFIG_BASE,
    CONFIG_STRESS,
    compute_metrics,
)

PERIODS = [
    ("BENCHMARK_90D_RECENT", 1784851200000, 1792659600000),
    ("BENCHMARK_90D_RETRO", 1721779200000, 1729587600000),
    ("IS", 1704067200000, 1751241600000),
    ("OOS", 1751241600000, 1792659600000),
    ("FULL", 1704067200000, 1792659600000),
]

SCENARIOS = [CONFIG_OPT, CONFIG_BASE, CONFIG_STRESS]


def run_all_native_tests():
    t_start = time.time()
    print("Initializing NativeEconomicRunner...")
    runner = NativeEconomicRunner()

    all_metrics: List[Dict[str, Any]] = []
    all_trades: List[Dict[str, Any]] = []
    all_equity: List[Dict[str, Any]] = []
    all_costs: List[Dict[str, Any]] = []

    strategies = [
        ("v3.1.4", runner.run_v314),
        ("v3.8", runner.run_v38),
        ("v3.2", runner.run_v32),
    ]

    for strat_name, run_func in strategies:
        print(f"\n==========================================", flush=True)
        print(f"Running Native Strategy: {strat_name}", flush=True)
        print(f"==========================================", flush=True)

        for scen in SCENARIOS:
            for period_name, start_ts, end_ts in PERIODS:
                print(f"  -> {strat_name} | {scen.name} | {period_name}...", flush=True)
                t0 = time.time()
                ledger, trades = run_func(scen, start_ts=start_ts, end_ts=end_ts)
                t_elapsed = time.time() - t0

                # Compute metrics
                m = compute_metrics(
                    strategy=strat_name,
                    scenario=scen.name,
                    period=period_name,
                    ledger=ledger,
                    trades=trades,
                )
                all_metrics.append(m)

                # Record trades
                for t in trades:
                    all_trades.append({
                        "trade_id": t.trade_id,
                        "strategy": strat_name,
                        "scenario": scen.name,
                        "period": period_name,
                        "symbol": t.symbol,
                        "direction": t.direction,
                        "entry_ts": t.entry_ts,
                        "exit_ts": t.exit_ts,
                        "entry_price": float(t.entry_price),
                        "exit_price": float(t.exit_price),
                        "qty": float(t.qty),
                        "gross_pnl": float(t.gross_pnl),
                        "entry_fee": float(t.entry_fee),
                        "exit_fee": float(t.exit_fee),
                        "funding_pnl": float(t.funding_pnl),
                        "net_pnl": float(t.net_pnl),
                        "exit_reason": t.exit_reason,
                        "hold_hours": t.hold_hours,
                        "trade_notional": float(t.trade_notional),
                    })

                # Sample equity curve (every 24 bars or halt points to avoid 100MB files)
                sample_interval = 24 if strat_name != "v3.1.4" else 6
                for idx, eq_pt in enumerate(ledger.equity_curve):
                    if idx % sample_interval == 0 or idx == len(ledger.equity_curve) - 1 or eq_pt.is_halted:
                        all_equity.append({
                            "timestamp": eq_pt.timestamp,
                            "strategy": strat_name,
                            "scenario": scen.name,
                            "period": period_name,
                            "equity": float(eq_pt.equity),
                            "cash": float(eq_pt.cash),
                            "unrealized_pnl": float(eq_pt.unrealized_pnl),
                            "margin_used": float(eq_pt.margin_used),
                            "available_margin": float(eq_pt.available_margin),
                            "drawdown_pct": float(eq_pt.drawdown_pct),
                            "is_halted": eq_pt.is_halted,
                        })

                # Cost attribution
                n_tr = len(trades)
                avg_fee = float(ledger.total_fees_paid) / n_tr if n_tr > 0 else 0.0
                fee_drag = (float(ledger.total_fees_paid) / float(ledger.initial_capital)) * 100.0
                all_costs.append({
                    "strategy": strat_name,
                    "scenario": scen.name,
                    "period": period_name,
                    "total_gross_pnl": float(ledger.total_gross_pnl),
                    "total_fees_paid": float(ledger.total_fees_paid),
                    "total_funding_pnl": float(ledger.total_funding_pnl),
                    "total_net_pnl": float(ledger.total_net_pnl),
                    "avg_fee_per_trade": avg_fee,
                    "fee_drag_pct_of_capital": fee_drag,
                    "total_trades": n_tr,
                })

                print(f"     Trades: {len(trades)}, Net PnL: ${m['net_pnl']:.2f}, Max DD: {m['max_drawdown_pct']:.2f}%, Verdict: {m['verdict']} ({t_elapsed:.2f}s)")

    # Export to CSVs
    reports_dir = REPO_ROOT / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    df_score = pd.DataFrame(all_metrics)
    score_path = reports_dir / "NATIVE_ECONOMIC_010R_SCOREBOARD.csv"
    df_score.to_csv(score_path, index=False)
    print(f"\nSaved {score_path} ({len(df_score)} rows)")

    df_tr = pd.DataFrame(all_trades)
    trades_path = reports_dir / "NATIVE_ECONOMIC_010R_TRADES.csv"
    df_tr.to_csv(trades_path, index=False)
    print(f"Saved {trades_path} ({len(df_tr)} rows)")

    df_eq = pd.DataFrame(all_equity)
    eq_path = reports_dir / "NATIVE_ECONOMIC_010R_EQUITY.csv"
    df_eq.to_csv(eq_path, index=False)
    print(f"Saved {eq_path} ({len(df_eq)} rows)")

    df_cost = pd.DataFrame(all_costs)
    cost_path = reports_dir / "NATIVE_ECONOMIC_010R_COSTS.csv"
    df_cost.to_csv(cost_path, index=False)
    print(f"Saved {cost_path} ({len(df_cost)} rows)")

    print(f"\nAll native economic tests completed in {time.time() - t_start:.2f}s.")
    return df_score


if __name__ == "__main__":
    run_all_native_tests()
