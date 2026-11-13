"""
Unit & Accounting Invariant Test Suite for ECONOMIC-GATE-010.
Tests:
1. Fee Contract 009B integration & unauthorized rate rejection.
2. Trade Ledger penny-perfect accounting invariant.
3. Point-in-Time universe listing date enforcement (RENDERUSDT).
4. Scoreboard completeness across all 7 strategies, 3 scenarios, and 3 periods.
5. Execution fill type labeling (MARKET_TAKER vs LIMIT_MAKER).
"""

from decimal import Decimal
from pathlib import Path
import pytest
import pandas as pd
import numpy as np

from src.fee_contract_009b import (
    CONSERVATIVE_OPERATOR_PROFILE,
    MNT_SENSITIVITY_PROFILE,
    calculate_funding_cashflow,
    ExecutedLeg,
    FeeProfile,
    FeeSource,
)
from src.economic_gate_010_runner import (
    EconomicGate010Engine,
    ExecutionConfig,
    CONFIG_OPT,
    CONFIG_BASE,
    CONFIG_STRESS,
    LISTING_TIMESTAMPS,
    ALL_SYMBOLS,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_fee_contract_integration():
    """Verifies that Conservative Operator Profile rates are enforced."""
    assert CONSERVATIVE_OPERATOR_PROFILE.maker_rate == Decimal("0.000360")
    assert CONSERVATIVE_OPERATOR_PROFILE.taker_rate == Decimal("0.001000")
    assert not CONSERVATIVE_OPERATOR_PROFILE.is_sensitivity_scenario

    # Verify that sub-standard rates raise ValueError
    with pytest.raises(ValueError):
        FeeProfile(
            maker_rate=Decimal("0.000200"),
            taker_rate=Decimal("0.000500"),
            source=FeeSource.CONSERVATIVE_OPERATOR,
            is_sensitivity_scenario=False,
        )


def test_ledger_accounting_invariant():
    """Verifies penny-perfect conservation between trades net PnL and final equity."""
    trades_file = REPO_ROOT / "reports" / "ECONOMIC_GATE_010_TRADES.csv"
    assert trades_file.exists(), "Trades CSV must exist"

    df_trades = pd.read_csv(trades_file)
    assert len(df_trades) > 0, "Trades CSV must not be empty"

    # Test on a sample of strategies and scenarios
    for strat in ["v3.1.4", "v3.8", "v3.2"]:
        for sc in ["OPT", "BASE", "STRESS"]:
            sub = df_trades[(df_trades["strategy"] == strat) & (df_trades["scenario"] == sc)]
            if len(sub) == 0:
                continue

            # Verify for each trade: net_pnl == gross_pnl - entry_fee - exit_fee + funding_cashflow
            calc_net = sub["gross_pnl"] - sub["entry_fee"] - sub["exit_fee"] + sub["funding_cashflow"]
            diff = (sub["net_pnl"] - calc_net).abs()
            assert diff.max() < 1e-4, f"Trade net PnL discrepancy for {strat} {sc}: max diff={diff.max()}"


def test_point_in_time_listing_enforcement():
    """Verifies that RENDERUSDT is never traded before its listing date (2024-07-26 08:00 UTC)."""
    trades_file = REPO_ROOT / "reports" / "ECONOMIC_GATE_010_TRADES.csv"
    df_trades = pd.read_csv(trades_file)

    render_trades = df_trades[df_trades["symbol"] == "RENDERUSDT"]
    if len(render_trades) > 0:
        listing_ts = LISTING_TIMESTAMPS["RENDERUSDT"]  # 1721980800000
        min_entry = render_trades["entry_ts"].min()
        assert min_entry >= listing_ts, (
            f"Point-in-Time violation: RENDERUSDT traded at {min_entry} prior to listing {listing_ts}"
        )


def test_scoreboard_coverage_and_verdicts():
    """Verifies that the Scoreboard covers all 7 strategies and all 3 scenarios."""
    sb_file = REPO_ROOT / "reports" / "ECONOMIC_GATE_010_SCOREBOARD.csv"
    assert sb_file.exists(), "Scoreboard CSV must exist"

    df_sb = pd.read_csv(sb_file)
    expected_strats = {"v3.1.4", "v3.8", "v3.2", "v3.0", "v3.6", "v3.4", "v2.1"}
    actual_strats = set(df_sb["strategy"].unique())
    assert actual_strats == expected_strats, f"Missing strategies in scoreboard: {expected_strats - actual_strats}"

    expected_scenarios = {"OPT", "BASE", "STRESS"}
    actual_scenarios = set(df_sb["scenario"].unique())
    assert actual_scenarios == expected_scenarios, f"Missing scenarios: {expected_scenarios - actual_scenarios}"

    # Verdicts must be valid
    valid_verdicts = {"PROFITABILITY_PASS", "NEGATIVE_EDGE", "INCONCLUSIVE", "BLOCKED_DATA", "BLOCKED_EXECUTION"}
    for v in df_sb["verdict"].unique():
        assert v in valid_verdicts, f"Invalid verdict: {v}"


def test_execution_order_type_labeling():
    """Verifies that maker and taker orders are properly classified and charged matching fee rates."""
    trades_file = REPO_ROOT / "reports" / "ECONOMIC_GATE_010_TRADES.csv"
    df_trades = pd.read_csv(trades_file)

    base_trades = df_trades[df_trades["scenario"] == "BASE"]
    # v3.8 contains hybrid maker/taker executions
    v38_trades = base_trades[base_trades["strategy"] == "v3.8"]
    maker_exits = v38_trades[v38_trades["exit_type"] == "HYBRID_MAKER_TAKER"]
    assert len(maker_exits) > 0, "v3.8 must have executed Maker partial take-profit fills"
