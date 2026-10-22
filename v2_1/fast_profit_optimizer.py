"""
Fast Parallel High-Conviction Profit Maximizer for Multi-Alpha Engine v2.1.
Evaluates the top parameter sets for maximum profit using Bybit 2024-2026 offline data.
"""

from decimal import Decimal
import logging
from pathlib import Path
import sys
import time
from typing import Any, Dict, List
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v2_1.src.config import DEFAULT_SYMBOLS
from v2_1.run_master_offline_test import load_and_prepare_4h_data, run_simulation, compute_metrics

logging.basicConfig(level=logging.WARNING)


def main():
    print("=" * 90)
    print(" FAST TARGETED PROFIT MAXIMIZATION TEST (BYBIT OFFLINE 2024-2026)")
    print("=" * 90)

    t0 = time.time()
    fast_data_4h, sorted_ts = load_and_prepare_4h_data(DEFAULT_SYMBOLS)
    print(f"Data prepared in {time.time()-t0:.1f}s.")

    # Top high-conviction candidate parameter profiles
    candidates = [
        {"name": "Profile 1 (ADX 20, Risk 1.5%, Def Pyr)", "risk": Decimal("0.015"), "adx": 20.0, "hard_dd": Decimal("30.0")},
        {"name": "Profile 2 (ADX 20, Risk 2.0%, Def Pyr)", "risk": Decimal("0.020"), "adx": 20.0, "hard_dd": Decimal("35.0")},
        {"name": "Profile 3 (ADX 20, Risk 2.5%, Def Pyr)", "risk": Decimal("0.025"), "adx": 20.0, "hard_dd": Decimal("35.0")},
        {"name": "Profile 4 (ADX 22, Risk 2.0%, Strong Trend)", "risk": Decimal("0.020"), "adx": 22.0, "hard_dd": Decimal("35.0")},
        {"name": "Profile 5 (ADX 22, Risk 2.5%, Strong Trend)", "risk": Decimal("0.025"), "adx": 22.0, "hard_dd": Decimal("35.0")},
        {"name": "Profile 6 (ADX 18, Risk 2.0%, Early Entry)", "risk": Decimal("0.020"), "adx": 18.0, "hard_dd": Decimal("35.0")},
        {"name": "Profile 7 (ADX 20, Risk 2.0%, Soft DD 12%)", "risk": Decimal("0.020"), "adx": 20.0, "hard_dd": Decimal("35.0"), "soft_dd": Decimal("12.0")},
        {"name": "Profile 8 (ADX 20, Risk 2.8%, High Kelly)", "risk": Decimal("0.028"), "adx": 20.0, "hard_dd": Decimal("35.0")},
    ]

    results = []

    for c in candidates:
        print(f"Running {c['name']}...", flush=True)
        soft_dd = c.get("soft_dd", Decimal("15.0"))
        t_eng, r_eng, eq_df = run_simulation(
            fast_data=fast_data_4h,
            sorted_ts=sorted_ts,
            mode=c["name"],
            base_risk=c["risk"],
            soft_dd=soft_dd,
            hard_dd=c["hard_dd"],
            min_adx=c["adx"],
            enable_range=False,
        )
        m = compute_metrics(t_eng, r_eng, eq_df)
        m["name"] = c["name"]
        m["risk"] = float(c["risk"] * 100)
        m["adx"] = c["adx"]
        results.append(m)

    print("\n" + "=" * 105)
    print(f"{'Profile Name':<42} | {'Final Eq ($)':<12} | {'Net Profit':<12} | {'ROI (%)':<10} | {'Max DD (%)':<10} | {'Sharpe':<8} | {'PF':<6}")
    print("-" * 105)

    # Sort by Final Equity descending
    results.sort(key=lambda x: x["final_equity"], reverse=True)

    for r in results:
        print(
            f"{r['name']:<42} | ${r['final_equity']:<11.2f} | ${r['net_profit']:<+11.2f} | "
            f"{r['net_return_pct']:<+9.2f}% | {r['max_drawdown']:<9.2f}% | {r['sharpe_ratio']:<7.2f} | {r['profit_factor']:<6.2f}"
        )

    best = results[0]
    print("\n" + "=" * 90)
    print(f"[CHAMPION] MAXIMUM PROFIT CHAMPION: {best['name']}")
    print(f"   Final Capital: ${best['final_equity']:.2f} (Net Profit: +${best['net_profit']:.2f} / +{best['net_return_pct']:.1f}% ROI)")
    print(f"   Max Drawdown: {best['max_drawdown']:.2f}% | Sharpe: {best['sharpe_ratio']:.2f} | Profit Factor: {best['profit_factor']:.2f} | Win Rate: {best['win_rate']:.1f}%")
    print(f"   Total Trades: {best['total_trades']} | Pyramiding Win Rate: {best['pyramided_win_rate']:.1f}%")
    print(f"   Annual Returns: 2024: {best['annual_returns'].get(2024, 0):+.1f}% | 2025: {best['annual_returns'].get(2025, 0):+.1f}% | 2026: {best['annual_returns'].get(2026, 0):+.1f}%")


if __name__ == "__main__":
    main()
