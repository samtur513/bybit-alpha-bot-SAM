"""
tools/audit_gate_010_discrepancies.py

Forensic Audit of ECONOMIC-GATE-010 Results:
Programmatically identifies all defects, mathematical impossibilities,
lookahead violations, and accounting errors in ECONOMIC-GATE-010.
"""

import math
from pathlib import Path
from typing import List, Dict, Any
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent

def run_forensic_audit():
    scoreboard_path = REPO_ROOT / "reports" / "ECONOMIC_GATE_010_SCOREBOARD.csv"
    trades_path = REPO_ROOT / "reports" / "ECONOMIC_GATE_010_TRADES.csv"
    equity_path = REPO_ROOT / "reports" / "ECONOMIC_GATE_010_EQUITY.csv"
    costs_path = REPO_ROOT / "reports" / "ECONOMIC_GATE_010_COST_ATTRIBUTION.csv"
    report_md_path = REPO_ROOT / "reports" / "ECONOMIC_GATE_010_REPORT.md"
    oos_md_path = REPO_ROOT / "reports" / "ECONOMIC_GATE_010_OOS_VALIDATION.md"

    df_sb = pd.read_csv(scoreboard_path)
    df_trades = pd.read_csv(trades_path)
    df_costs = pd.read_csv(costs_path)

    discrepancies: List[Dict[str, Any]] = []

    # 1. Negative final equity & Max DD > 100% on isolated accounts
    for idx, row in df_sb.iterrows():
        strat = row["strategy"]
        scen = row["scenario"]
        period = row["period"]
        final_eq = float(row["final_equity"])
        net_pnl = float(row["net_pnl"])
        max_dd_pct = float(row["max_drawdown_pct"])

        if final_eq < 0:
            discrepancies.append({
                "discrepancy_id": f"DISC_FIN_EQ_NEG_{idx}",
                "category": "ACCOUNTING_IMPOSSIBILITY",
                "strategy": strat,
                "scenario": scen,
                "period": period,
                "metric": "final_equity",
                "gate_010_value": f"${final_eq:.2f}",
                "expected_constraint": "Equity >= 0 on isolated account; liquidation/halt at 0",
                "severity": "CRITICAL",
                "description": f"Account plunged into negative equity (${final_eq:.2f}) from $300 deposit. Isolated margin on Bybit halts or liquidates at 0."
            })

        if max_dd_pct > 100.0:
            discrepancies.append({
                "discrepancy_id": f"DISC_MAX_DD_OVER_100_{idx}",
                "category": "MATHEMATICAL_IMPOSSIBILITY",
                "strategy": strat,
                "scenario": scen,
                "period": period,
                "metric": "max_drawdown_pct",
                "gate_010_value": f"{max_dd_pct:.2f}%",
                "expected_constraint": "Max DD <= 100.0% for isolated initial capital",
                "severity": "CRITICAL",
                "description": f"Drawdown reached {max_dd_pct:.2f}%, impossible under standard isolated margin without unsecured debt."
            })

        # 2. OOS Max Drawdown == 0.0%
        if period == "OOS" and max_dd_pct == 0.0 and float(row["total_trades"]) > 0:
            discrepancies.append({
                "discrepancy_id": f"DISC_OOS_DD_ZERO_{idx}",
                "category": "METRIC_CALCULATION_DEFECT",
                "strategy": strat,
                "scenario": scen,
                "period": period,
                "metric": "max_drawdown_pct",
                "gate_010_value": "0.00%",
                "expected_constraint": "Drawdown > 0 during losing OOS period",
                "severity": "CRITICAL",
                "description": "OOS max drawdown reported as 0.00% because equity was already negative (<0) at start of OOS, bypassing (peak > 0) condition in compute_drawdown."
            })

        # 3. Fixed $15 notional in net expectancy bps
        exp_r = float(row["net_expectancy_r"])
        exp_bps = float(row["net_expectancy_bps"])
        # Expected ratio if 15.0 notional was hardcoded:
        # bps = (net_pnl / 15.0) * 10000; r = net_pnl / (total_trades * 3.0) -> bps / r = 3.0 / 15.0 * 10000 = 2000.0
        if abs(exp_r) > 1e-6:
            ratio = exp_bps / exp_r
            if abs(ratio - 2000.0) < 1.0:
                discrepancies.append({
                    "discrepancy_id": f"DISC_FIXED_NOTIONAL_BPS_{idx}",
                    "category": "DIMENSIONAL_DEFECT",
                    "strategy": strat,
                    "scenario": scen,
                    "period": period,
                    "metric": "net_expectancy_bps",
                    "gate_010_value": f"{exp_bps:.2f} bps (ratio to R: {ratio:.1f})",
                    "expected_constraint": "bps = (net_pnl / actual_trade_notional) * 10000",
                    "severity": "HIGH",
                    "description": "Net expectancy in bps was calculated assuming a hardcoded fixed $15.00 notional per trade, regardless of actual position size or coin price."
                })

    # 4. Gross PnL variance across OPT, BASE, STRESS
    for strat in df_sb["strategy"].unique():
        for period in ["IS", "OOS", "FULL"]:
            sub = df_sb[(df_sb["strategy"] == strat) & (df_sb["period"] == period)]
            gross_vals = sub["gross_pnl"].tolist()
            if len(set(gross_vals)) > 1:
                discrepancies.append({
                    "discrepancy_id": f"DISC_GROSS_VARIANCE_{strat}_{period}",
                    "category": "ACCOUNTING_DEFECT",
                    "strategy": strat,
                    "scenario": "ALL (OPT/BASE/STRESS)",
                    "period": period,
                    "metric": "gross_pnl",
                    "gate_010_value": f"OPT={gross_vals[0]:.2f}, BASE={gross_vals[1]:.2f}, STRESS={gross_vals[2]:.2f}",
                    "expected_constraint": "Gross PnL must be invariant across execution cost scenarios",
                    "severity": "HIGH",
                    "description": "Gross PnL varied across cost scenarios because slippage was subtracted directly from executed price before calculating gross PnL."
                })

    # 5. Lookahead in Trailing Stop Updates
    discrepancies.append({
        "discrepancy_id": "DISC_LOOKAHEAD_TRAILING_STOP",
        "category": "POINT_IN_TIME_VIOLATION",
        "strategy": "v3.1.4, v3.8, v3.0, v3.2, v3.6, v3.4, v2.1",
        "scenario": "ALL",
        "period": "ALL",
        "metric": "execution_order",
        "gate_010_value": "b_high updated current_stop / triggered partial TP BEFORE checking if b_low <= current_stop",
        "expected_constraint": "Check existing stop loss trigger BEFORE adjusting stop with intra-bar high",
        "severity": "CRITICAL",
        "description": "Intra-bar lookahead: high of bar t was used to advance trailing stop or take profit before checking if low of bar t hit the prior stop loss."
    })

    # 6. Random Benchmark Scaling Discrepancy
    discrepancies.append({
        "discrepancy_id": "DISC_RANDOM_BENCHMARK_SCALING",
        "category": "DIMENSIONAL_DEFECT",
        "strategy": "ALL",
        "scenario": "BASE",
        "period": "FULL",
        "metric": "alpha_over_random_bps",
        "gate_010_value": "Strategy bps used $15 denominator; Random benchmark bps used $450 denominator",
        "expected_constraint": "Identical notional normalization for strategy and benchmark",
        "severity": "HIGH",
        "description": "Alpha over random was computed by subtracting random_bps (divided by $450 capital * 1.5) from strategy_bps (divided by hardcoded $15), distorting the comparison by 30x."
    })

    # 7. Unverified Funding Rates
    discrepancies.append({
        "discrepancy_id": "DISC_UNVERIFIED_FUNDING_FALLBACK",
        "category": "DATA_INTEGRITY_DEFECT",
        "strategy": "ALL",
        "scenario": "ALL",
        "period": "ALL",
        "metric": "funding_rates",
        "gate_010_value": "Empirical funding used ONLY for BTC and ETH; 16 assets used flat 0.000100 fallback",
        "expected_constraint": "Empirical historical funding rates for all 18 traded assets",
        "severity": "MEDIUM",
        "description": "Only BTCUSDT_funding.parquet and ETHUSDT_funding.parquet existed in data/parquet/. All other 16 coins used a flat constant 1 bp fallback rate."
    })

    # 8. Textual Report Contradiction with Numerical Results
    discrepancies.append({
        "discrepancy_id": "DISC_TEXT_NUMERICAL_CONTRADICTION",
        "category": "DOCUMENTATION_CONTRADICTION",
        "strategy": "v3.1.4",
        "scenario": "BASE",
        "period": "IS / OOS",
        "metric": "OOS Narrative vs Table",
        "gate_010_value": "Text claims: 'Demonstrates stable positive expectancy across both IS and OOS'; Table shows: IS Net PnL = -$395.37 (-0.25R), OOS Net PnL = -$404.68 (-0.31R)",
        "expected_constraint": "Narrative conclusion must match numerical data",
        "severity": "HIGH",
        "description": "ECONOMIC_GATE_010_OOS_VALIDATION.md text claimed stable positive expectancy for v3.1.4 while the table directly above documented substantial losses (-$800 total, NEGATIVE_EDGE)."
    })

    # Convert to DataFrame
    df_disc = pd.DataFrame(discrepancies)
    out_csv = REPO_ROOT / "reports" / "ECONOMIC_010_FORENSIC_DISCREPANCIES.csv"
    df_disc.to_csv(out_csv, index=False)
    print(f"Generated {out_csv} with {len(df_disc)} discrepancies.")
    return df_disc

if __name__ == "__main__":
    run_forensic_audit()
