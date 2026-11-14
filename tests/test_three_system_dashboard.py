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


def test_three_system_dashboard_real_portfolio_instance():
    """Verify that dashboard works natively with real PortfolioInstance objects."""
    from v3_2.src.shadow.paper_trader_v32_shadow import PortfolioInstance

    real_native = PortfolioInstance(
        name="NATIVE",
        initial_capital=Decimal("300.0"),
        taker_fee=Decimal("0.0009"),
        maker_fee=Decimal("0.000324"),
        use_spread_gate=True,
        use_depth_slippage=False,
    )
    real_shadow = PortfolioInstance(
        name="SHADOW",
        initial_capital=Decimal("300.0"),
        taker_fee=Decimal("0.0010"),
        maker_fee=Decimal("0.0004"),
        use_spread_gate=True,
        use_depth_slippage=True,
    )

    # Simulate some changes in real portfolios
    real_native.realized_equity = Decimal("305.50")
    real_native.total_fees = Decimal("0.85")
    real_native.total_funding = Decimal("-0.12")
    real_native.peak_equity = Decimal("306.00")

    real_shadow.realized_equity = Decimal("302.20")
    real_shadow.total_fees = Decimal("1.10")
    real_shadow.total_funding = Decimal("-0.12")
    real_shadow.peak_equity = Decimal("303.00")

    dash = ThreeSystemComparativeDashboard()
    report = dash.generate_comparative_report(
        native_portfolio=real_native,
        shadow_portfolio=real_shadow,
        r2_result=None,
    )

    sys_a = report["systems"]["native"]
    assert sys_a["equity_usdt"] == 305.50
    assert sys_a["fees_paid_usdt"] == 0.85
    assert sys_a["funding_paid_usdt"] == -0.12
    assert sys_a["data_freshness_status"] == "FRESH"
    assert sys_a["valuation_status"] == "FRESH"

    sys_b = report["systems"]["shadow"]
    assert sys_b["equity_usdt"] == 302.20
    assert sys_b["fees_paid_usdt"] == 1.10
    assert sys_b["funding_paid_usdt"] == -0.12

    # R2 not provided -> NOT_STARTED and None
    sys_c = report["systems"]["r2_shadow"]
    assert sys_c["equity_usdt"] is None
    assert sys_c["data_freshness_status"] == "NOT_STARTED"
    assert sys_c["valuation_status"] == "NOT_STARTED"


def test_three_system_dashboard_unstarted_fictitious_elimination():
    """Verify that unstarted systems do NOT return fake 300 USDT or 0% PnL."""
    dash = ThreeSystemComparativeDashboard()

    # Pass None or native_started=False
    report = dash.generate_comparative_report(
        native_portfolio=None,
        shadow_portfolio=None,
        r2_result=None,
        native_started=False,
        shadow_started=False,
        r2_started=False,
    )

    for sys_key in ["native", "shadow", "r2_shadow"]:
        sys = report["systems"][sys_key]
        assert sys["equity_usdt"] is None, f"{sys_key} equity must be None when not started"
        assert sys["net_pnl_usdt"] is None, f"{sys_key} net_pnl must be None when not started"
        assert sys["net_return_pct"] is None, f"{sys_key} return must be None when not started"
        assert sys["mdd_pct"] is None, f"{sys_key} mdd must be None when not started"
        assert sys["cash_usdt"] is None, f"{sys_key} cash must be None when not started"
        assert sys["data_freshness_status"] == "NOT_STARTED"
        assert sys["valuation_status"] == "NOT_STARTED"


def test_three_system_dashboard_cohort_mismatch_detection():
    """Verify cohort validation detects mismatched cohort id."""
    dash = ThreeSystemComparativeDashboard(expected_cohort_id="CANONICAL_COHORT_123")
    native_port = MockPortfolio(eq=300.0)

    report = dash.generate_comparative_report(
        native_portfolio=native_port,
        cohort_id="INVALID_COHORT_999",
    )

    assert report["target_cohort"]["cohort_status"] == "COHORT_MISMATCH"
    assert report["systems"]["native"]["data_freshness_status"] == "COHORT_MISMATCH"
    assert report["systems"]["native"]["error_count"] > 0


def test_three_system_dashboard_http_server():
    """Verify that ThreeSystemDashboardHTTPHandler correctly serves HTML, JSON, /health, and 405 on POST."""
    import threading
    import urllib.request
    from v3_2.src.integrations.three_system_dashboard import create_dashboard_server

    dash = ThreeSystemComparativeDashboard()
    server = create_dashboard_server(dashboard_provider=dash, host="127.0.0.1", port=0)
    actual_port = server.server_address[1]

    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{actual_port}"
    try:
        # 1. GET / -> HTML
        req = urllib.request.Request(f"{base_url}/")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            html_body = resp.read().decode("utf-8")
            assert "TORG-BOT Three-System Comparative Dashboard" in html_body
            assert "text/html" in resp.headers.get("Content-Type", "")

        # 2. GET /api/status -> JSON
        req = urllib.request.Request(f"{base_url}/api/status")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            import json
            data = json.loads(resp.read().decode("utf-8"))
            assert "systems" in data
            assert "target_cohort" in data
            assert data["target_cohort"]["cohort_id"] == "TORG_THREE_SYS_20261115_001"

        # 3. GET /health -> 200
        req = urllib.request.Request(f"{base_url}/health")
        with urllib.request.urlopen(req) as resp:
            assert resp.status == 200
            import json
            health_data = json.loads(resp.read().decode("utf-8"))
            assert health_data["status"] == "ok"

        # 4. POST /api/status -> 405 Method Not Allowed
        post_req = urllib.request.Request(f"{base_url}/api/status", data=b"{}", method="POST")
        try:
            urllib.request.urlopen(post_req)
            assert False, "POST should fail with 405"
        except urllib.error.HTTPError as err:
            assert err.code == 405

    finally:
        server.shutdown()
        server.server_close()

