"""
tests/test_three_system_dashboard.py
Unit tests for ThreeSystemComparativeDashboard.
Protocol: TORG-BOT — THREE-SYSTEM-INTEGRATION-005
"""

from datetime import datetime, timezone
from decimal import Decimal
import pytest

from v3_2.src.integrations.three_system_dashboard import ThreeSystemComparativeDashboard
from v3_2.src.integrations.r2_consumer import R2ConsumerResult


class MockPortfolio:
    def __init__(self, eq=300.0, bal=300.0, fees=0.0, funding=0.0):
        self.realized_equity = Decimal(str(eq))
        self.balance = Decimal(str(bal))
        self.initial_capital = Decimal("300.0")
        self.total_fees_paid = Decimal(str(fees))
        self.total_funding_paid = Decimal(str(funding))
        self.positions = {}
        self.total_turnover = Decimal("0.0")


def test_three_system_dashboard_independent_metrics():
    dash = ThreeSystemComparativeDashboard()
    
    native_port = MockPortfolio(eq=302.5, bal=250.0, fees=0.15, funding=0.02)
    shadow_port = MockPortfolio(eq=301.8, bal=250.0, fees=0.20, funding=0.02)
    
    r2_res = R2ConsumerResult(
        is_valid=True,
        status="OBSERVING",
        r2_base_profile="R2:300:BASE:OBSERVED_BOOK",
        valuation_status="FRESH",
        equity_usdt=304.10,
        net_pnl_usdt=4.10,
        net_return_pct=1.37,
        mdd_pct=-0.45,
        cash_usdt=180.0,
        exposure_pct=40.0,
        r2_minus_t01_usdt=2.10,
        r2_minus_t01_pp=0.70,
        comparison_status="PAIRED_PRELIMINARY",
        fees_usdt=0.30,
        buffer_usdt=0.10,
        spread_impact_usdt=0.20,
        turnover_usdt=250.0,
        quantities={"BTCUSDT": 0.0015},
    )
    
    report = dash.generate_comparative_report(
        native_portfolio=native_port,
        shadow_portfolio=shadow_port,
        r2_result=r2_res,
        now_dt=datetime(2026, 11, 15, 0, 0, 0, tzinfo=timezone.utc),
    )
    
    assert report["separation_rules"]["spot_perp_separation_enforced"] is True
    assert report["separation_rules"]["portfolio_pooling_forbidden"] is True
    
    sys_a = report["systems"]["native"]
    sys_b = report["systems"]["shadow"]
    sys_c = report["systems"]["r2_shadow"]
    
    assert sys_a["market_type"] == "BYBIT_LINEAR_PERPETUAL"
    assert sys_a["equity_usdt"] == 302.5
    assert sys_a["net_pnl_usdt"] == 2.5
    
    assert sys_b["market_type"] == "BYBIT_LINEAR_PERPETUAL"
    assert sys_b["equity_usdt"] == 301.8
    assert sys_b["net_pnl_usdt"] == 1.8
    
    assert sys_c["market_type"] == "BYBIT_SPOT_LONG_CASH_NO_LEVERAGE"
    assert sys_c["equity_usdt"] == 304.10
    assert sys_c["extra"]["r2_minus_t01_usdt"] == 2.10
    assert sys_c["extra"]["r2_minus_t01_pp"] == 0.70


def test_three_system_dashboard_handles_unvalued_r2():
    dash = ThreeSystemComparativeDashboard()
    
    native_port = MockPortfolio(eq=300.0, bal=300.0)
    shadow_port = MockPortfolio(eq=300.0, bal=300.0)
    
    r2_unvalued = R2ConsumerResult(
        is_valid=True,
        status="NO_FRESH_HEARTBEAT",
        r2_base_profile="R2:300:BASE:OBSERVED_BOOK",
        valuation_status="UNVALUED_OR_STALE",
        equity_usdt=None,
        net_pnl_usdt=None,
        net_return_pct=None,
        mdd_pct=0.0,
        cash_usdt=300.0,
    )
    
    report = dash.generate_comparative_report(
        native_portfolio=native_port,
        shadow_portfolio=shadow_port,
        r2_result=r2_unvalued,
    )
    
    sys_c = report["systems"]["r2_shadow"]
    assert sys_c["equity_usdt"] is None
    assert sys_c["valuation_status"] == "UNVALUED_OR_STALE"
