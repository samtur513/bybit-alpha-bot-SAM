"""
v3_2/src/integrations/three_system_dashboard.py
Unified Three-System Comparative Dashboard Provider & Read-Only HTTP Server.
Protocol: TORG-BOT — THREE-SYSTEM-LAUNCH-GATE-006 / THREE-SYSTEM-INTEGRATION-005

Aggregates three strictly independent accounts ($300.000000 USDT each):
1. System A: Bybit Multi-Alpha v3.2 Native (Linear Perpetual, Original Trend Logic)
2. System B: Bybit Multi-Alpha v3.2 Realistic Shadow (Linear Perpetual, Discrete L2 Execution & 8h Funding)
3. System C: CryptoLab R2-SHADOW (Bybit Spot Long-Only Cash, Profiles: R2 BASE, R2 STRESS, BASELINE T01)

Core Invariants:
- NEVER pools or sums Spot and Perpetual PnL into a single portfolio.
- NEVER modifies original v3.2 on Port 8083.
- NEVER shows fictitious 300 USDT, 0% PnL, or FRESH when data source is unconfirmed or not started.
- Read-only HTTP interface (GET / for HTML, GET /api/status for JSON, 405 for mutations).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import logging
from pathlib import Path
import socket
from typing import Any, Dict, List, Optional

from v3_2.src.integrations.r2_consumer import (
    R2TelemetryConsumer,
    R2ConsumerResult,
    parse_iso_utc,
)

logger = logging.getLogger("ThreeSystemDashboard")

CANONICAL_COHORT_ID = "TORG_THREE_SYS_20261115_001"
CANONICAL_T0_UTC = "2026-11-17T00:00:00+00:00"


@dataclass
class SystemMetrics:
    """Standardized comparative metrics for each independent system."""
    name: str
    role: str
    market_type: str  # BYBIT_LINEAR_PERPETUAL or BYBIT_SPOT_LONG_CASH_NO_LEVERAGE
    initial_capital_usdt: float
    equity_usdt: Optional[float]
    net_pnl_usdt: Optional[float]
    net_return_pct: Optional[float]
    mdd_pct: Optional[float]
    cash_usdt: Optional[float]
    exposure_pct: Optional[float]
    turnover_usdt: Optional[float]
    fees_paid_usdt: Optional[float]
    slippage_cost_usdt: Optional[float]
    funding_paid_usdt: Optional[float]
    positions_count: int
    positions_detail: Dict[str, Any]
    data_freshness_status: str  # FRESH, STALE, NOT_STARTED, NO_FRESH_HEARTBEAT, UNKNOWN
    valuation_status: str       # FRESH, UNVALUED_OR_STALE, NOT_STARTED
    error_count: int
    last_update_iso: Optional[str]
    notes: str
    is_active: bool = False
    cohort_id: Optional[str] = None
    t0_utc: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


class ThreeSystemComparativeDashboard:
    """
    Unified telemetry provider that renders comparative status across
    Native, Realistic Shadow, and CryptoLab R2-SHADOW.
    """

    def __init__(
        self,
        r2_consumer: Optional[R2TelemetryConsumer] = None,
        r2_telemetry_path: Optional[Path] = None,
        expected_cohort_id: str = CANONICAL_COHORT_ID,
        expected_t0_utc: str = CANONICAL_T0_UTC,
    ):
        self.r2_consumer = r2_consumer or R2TelemetryConsumer()
        self.r2_telemetry_path = r2_telemetry_path
        self.expected_cohort_id = expected_cohort_id
        self.expected_t0_utc = expected_t0_utc

    def _extract_perp_metrics(
        self,
        portfolio: Any,
        name: str,
        role: str,
        fee_tier_note: str,
        now_dt: Optional[datetime] = None,
        is_started: bool = True,
        cohort_id: Optional[str] = None,
        t0_utc: Optional[str] = None,
    ) -> SystemMetrics:
        """
        Extracts metrics from a perpetual portfolio instance (Native or Shadow).
        Supports real PortfolioInstance, PaperTraderV32, or mock objects without attribute mismatch.
        If portfolio is None or not started, returns unverified/NOT_STARTED state with None values.
        """
        # Check if portfolio is not started or has no equity valuation yet
        raw_eq = getattr(portfolio, "realized_equity", None)
        port_is_started = getattr(portfolio, "is_started", True)
        port_status = getattr(portfolio, "status", None)

        if portfolio is None or not is_started or not port_is_started or raw_eq is None or port_status in ("NOT_STARTED", "NO_DATA"):
            status_val = port_status or "NOT_STARTED"
            return SystemMetrics(
                name=name,
                role=role,
                market_type="BYBIT_LINEAR_PERPETUAL",
                initial_capital_usdt=300.0,
                equity_usdt=None,
                net_pnl_usdt=None,
                net_return_pct=None,
                mdd_pct=None,
                cash_usdt=None,
                exposure_pct=None,
                turnover_usdt=None,
                fees_paid_usdt=None,
                slippage_cost_usdt=None,
                funding_paid_usdt=None,
                positions_count=0,
                positions_detail={},
                data_freshness_status=status_val,
                valuation_status=status_val,
                error_count=0,
                last_update_iso=getattr(portfolio, "last_update_iso", None),
                notes=f"{name}: {status_val} (Awaiting verified telemetry)",
                is_active=False,
                cohort_id=cohort_id or self.expected_cohort_id,
                t0_utc=t0_utc or self.expected_t0_utc,
            )

        # 1. Equity & Capital
        init_cap = float(getattr(portfolio, "initial_capital", 300.0))
        eq = float(raw_eq)
        pnl = eq - init_cap
        ret = (pnl / init_cap) * 100.0 if init_cap > 0 else 0.0

        # 2. Accounting fields compatibility: total_fees vs total_fees_paid
        if hasattr(portfolio, "total_fees"):
            fees = float(portfolio.total_fees)
        elif hasattr(portfolio, "total_fees_paid"):
            fees = float(portfolio.total_fees_paid)
        else:
            fees = 0.0

        # Accounting fields compatibility: total_funding vs total_funding_paid
        if hasattr(portfolio, "total_funding"):
            funding = float(portfolio.total_funding)
        elif hasattr(portfolio, "total_funding_paid"):
            funding = float(portfolio.total_funding_paid)
        else:
            funding = 0.0

        # 3. Cash balance compatibility
        # If portfolio has .balance, use it; else calculate free cash from equity and margin
        if hasattr(portfolio, "balance"):
            cash = float(portfolio.balance)
        else:
            positions_map = getattr(portfolio, "positions", {})
            used_margin = sum(
                float(p.current_qty) * float(p.entry_price) / 3.5
                for p in positions_map.values()
                if hasattr(p, "current_qty") and hasattr(p, "entry_price")
            )
            cash = max(0.0, eq - used_margin)

        # 4. Positions & Exposure
        positions_map = getattr(portfolio, "positions", {})
        pos_detail = {}
        total_notional = 0.0
        for sym, p in positions_map.items():
            qty = float(getattr(p, "current_qty", 0.0))
            ep = float(getattr(p, "entry_price", 0.0))
            pos_detail[sym] = {
                "direction": "LONG" if getattr(p, "direction", 1) == 1 else "SHORT",
                "qty": qty,
                "entry_price": ep,
                "current_stop": float(getattr(p, "current_stop", 0.0)),
                "tp1_taken": getattr(p, "tp1_taken", False),
                "tp2_taken": getattr(p, "tp2_taken", False),
            }
            total_notional += qty * ep

        pos_cnt = len(pos_detail)
        exposure = round((total_notional / eq) * 100.0, 2) if eq > 0 else 0.0

        # 5. Peak Equity & Drawdown
        peak = float(getattr(portfolio, "peak_equity", eq))
        if peak > 0 and peak >= eq:
            mdd = round(((eq - peak) / peak) * 100.0, 2)
            mdd = min(0.0, mdd)
        else:
            mdd = 0.0

        # 6. Turnover
        turnover = 0.0
        if hasattr(portfolio, "total_turnover") and portfolio.total_turnover:
            turnover = float(portfolio.total_turnover)
        elif hasattr(portfolio, "closed_trades"):
            for t in portfolio.closed_trades:
                iq = float(getattr(t, "initial_qty", 0.0))
                ep = float(getattr(t, "entry_price", 0.0))
                fq = float(getattr(t, "final_qty", 0.0))
                xp = float(getattr(t, "exit_price", 0.0))
                turnover += (iq * ep) + (fq * xp)
            turnover += total_notional

        # 7. Cohort verification
        c_id = cohort_id or getattr(portfolio, "cohort_id", self.expected_cohort_id)
        t0 = t0_utc or getattr(portfolio, "t0_utc", self.expected_t0_utc)
        cohort_match = (c_id == self.expected_cohort_id)

        now_str = (now_dt or datetime.now(timezone.utc)).isoformat()

        return SystemMetrics(
            name=name,
            role=role,
            market_type="BYBIT_LINEAR_PERPETUAL",
            initial_capital_usdt=round(init_cap, 4),
            equity_usdt=round(eq, 4),
            net_pnl_usdt=round(pnl, 4),
            net_return_pct=round(ret, 3),
            mdd_pct=mdd,
            cash_usdt=round(cash, 4),
            exposure_pct=exposure,
            turnover_usdt=round(turnover, 2),
            fees_paid_usdt=round(fees, 4),
            slippage_cost_usdt=0.0,
            funding_paid_usdt=round(funding, 4),
            positions_count=pos_cnt,
            positions_detail=pos_detail,
            data_freshness_status="FRESH" if cohort_match else "COHORT_MISMATCH",
            valuation_status="FRESH",
            error_count=0 if cohort_match else 1,
            last_update_iso=now_str,
            notes=fee_tier_note if cohort_match else f"COHORT MISMATCH: expected {self.expected_cohort_id}, got {c_id}",
            is_active=True,
            cohort_id=c_id,
            t0_utc=t0,
        )

    def build_system_a_native_metrics(
        self,
        native_portfolio: Any,
        now_dt: Optional[datetime] = None,
        is_started: bool = True,
        cohort_id: Optional[str] = None,
        t0_utc: Optional[str] = None,
    ) -> SystemMetrics:
        """Extracts metrics from v3.2 Native portfolio."""
        return self._extract_perp_metrics(
            portfolio=native_portfolio,
            name="v3.2 Native",
            role="Original Donchian Trend Logic (New Isolated Paper Cohort)",
            fee_tier_note="Linear Perpetuals, Half-Kelly sizing, 0.09% MNT taker fee tier",
            now_dt=now_dt,
            is_started=is_started,
            cohort_id=cohort_id,
            t0_utc=t0_utc,
        )

    def build_system_b_shadow_metrics(
        self,
        shadow_portfolio: Any,
        now_dt: Optional[datetime] = None,
        is_started: bool = True,
        cohort_id: Optional[str] = None,
        t0_utc: Optional[str] = None,
    ) -> SystemMetrics:
        """Extracts metrics from v3.2 Realistic Shadow portfolio."""
        return self._extract_perp_metrics(
            portfolio=shadow_portfolio,
            name="v3.2 Realistic Shadow",
            role="Original Trend Logic with Discrete L2 Orderbook & 8h Funding",
            fee_tier_note="Linear Perpetuals, Discrete L2 orderbook ladder walking, 0.10% standard retail fee",
            now_dt=now_dt,
            is_started=is_started,
            cohort_id=cohort_id,
            t0_utc=t0_utc,
        )

    def build_system_c_r2_metrics(
        self,
        r2_result: Optional[R2ConsumerResult] = None,
        now_dt: Optional[datetime] = None,
        is_started: bool = True,
    ) -> SystemMetrics:
        """
        Extracts metrics from CryptoLab R2-SHADOW consumer result.
        Strictly enforces UNKNOWN/STALE/NOT_STARTED and never displays fictitious equity.
        """
        if r2_result is None and self.r2_telemetry_path and self.r2_telemetry_path.exists():
            r2_result = self.r2_consumer.read_telemetry_file(self.r2_telemetry_path, now_dt=now_dt)

        if not is_started or r2_result is None or not r2_result.is_valid:
            reason = r2_result.rejection_reason if r2_result else "Awaiting telemetry file"
            status = "NOT_STARTED" if not is_started else ("INVALID" if r2_result else "UNKNOWN")
            return SystemMetrics(
                name="CryptoLab R2-SHADOW",
                role="Spot Long-Only Cash Microstructure Alpha (Hunter/R2)",
                market_type="BYBIT_SPOT_LONG_CASH_NO_LEVERAGE",
                initial_capital_usdt=300.0,
                equity_usdt=None,
                net_pnl_usdt=None,
                net_return_pct=None,
                mdd_pct=None,
                cash_usdt=None,
                exposure_pct=None,
                turnover_usdt=None,
                fees_paid_usdt=None,
                slippage_cost_usdt=None,
                funding_paid_usdt=0.0,
                positions_count=0,
                positions_detail={},
                data_freshness_status=status,
                valuation_status="NOT_STARTED" if not is_started else "UNVALUED_OR_STALE",
                error_count=0 if not is_started else 1,
                last_update_iso=None,
                notes=f"CryptoLab R2: {reason}",
                is_active=False,
                cohort_id=self.expected_cohort_id,
                t0_utc=self.expected_t0_utc,
            )

        # If deployment_status is NOT_DEPLOYED, equity is unconfirmed
        if r2_result.deployment_status == "NOT_DEPLOYED" and r2_result.equity_usdt is None:
            return SystemMetrics(
                name="CryptoLab R2-SHADOW",
                role="Spot Long-Only Cash Microstructure Alpha (Hunter/R2)",
                market_type="BYBIT_SPOT_LONG_CASH_NO_LEVERAGE",
                initial_capital_usdt=300.0,
                equity_usdt=None,
                net_pnl_usdt=None,
                net_return_pct=None,
                mdd_pct=None,
                cash_usdt=r2_result.cash_usdt,
                exposure_pct=None,
                turnover_usdt=r2_result.turnover_usdt,
                fees_paid_usdt=r2_result.fees_usdt,
                slippage_cost_usdt=r2_result.spread_impact_usdt,
                funding_paid_usdt=0.0,
                positions_count=len([q for q in r2_result.quantities.values() if q > 0]),
                positions_detail=r2_result.quantities,
                data_freshness_status="NOT_STARTED" if r2_result.status == "NO_FRESH_HEARTBEAT" else r2_result.status,
                valuation_status=r2_result.valuation_status,
                error_count=r2_result.error_count,
                last_update_iso=r2_result.generated_at.isoformat() if r2_result.generated_at else None,
                notes="CryptoLab R2: NOT_DEPLOYED (Awaiting T0 activation)",
                is_active=False,
                cohort_id=self.expected_cohort_id,
                t0_utc=r2_result.cohort_t0_utc or self.expected_t0_utc,
            )

        # Active, verified live telemetry
        return SystemMetrics(
            name="CryptoLab R2-SHADOW",
            role="Spot Long-Only Cash Microstructure Alpha (Hunter/R2)",
            market_type="BYBIT_SPOT_LONG_CASH_NO_LEVERAGE",
            initial_capital_usdt=300.0,
            equity_usdt=r2_result.equity_usdt,
            net_pnl_usdt=r2_result.net_pnl_usdt,
            net_return_pct=r2_result.net_return_pct,
            mdd_pct=r2_result.mdd_pct,
            cash_usdt=r2_result.cash_usdt,
            exposure_pct=r2_result.exposure_pct,
            turnover_usdt=r2_result.turnover_usdt,
            fees_paid_usdt=r2_result.fees_usdt,
            slippage_cost_usdt=r2_result.spread_impact_usdt,
            funding_paid_usdt=0.0,
            positions_count=len([q for q in r2_result.quantities.values() if q > 0]),
            positions_detail=r2_result.quantities,
            data_freshness_status=r2_result.status,
            valuation_status=r2_result.valuation_status,
            error_count=r2_result.error_count,
            last_update_iso=r2_result.generated_at.isoformat() if r2_result.generated_at else None,
            notes="Spot Cash (No Leverage, No Funding), 3 profiles (R2 BASE, R2 STRESS, BASELINE T01)",
            is_active=(r2_result.status in ["ARMED", "OBSERVING"]),
            cohort_id=self.expected_cohort_id,
            t0_utc=r2_result.cohort_t0_utc or self.expected_t0_utc,
            extra={
                "r2_minus_t01_usdt": r2_result.r2_minus_t01_usdt,
                "r2_minus_t01_pp": r2_result.r2_minus_t01_pp,
                "comparison_status": r2_result.comparison_status,
                "buffer_usdt": r2_result.buffer_usdt,
                "real_orders": r2_result.real_orders,
                "demo_orders": r2_result.demo_orders,
                "deployment_status": r2_result.deployment_status,
            },
        )

    def generate_comparative_report(
        self,
        native_portfolio: Any = None,
        shadow_portfolio: Any = None,
        r2_result: Optional[R2ConsumerResult] = None,
        now_dt: Optional[datetime] = None,
        native_started: Optional[bool] = None,
        shadow_started: Optional[bool] = None,
        r2_started: Optional[bool] = None,
        cohort_id: Optional[str] = None,
        t0_utc: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generates unified multi-system telemetry dictionary."""
        effective_cohort = cohort_id or self.expected_cohort_id
        effective_t0 = t0_utc or self.expected_t0_utc

        native_act = (native_portfolio is not None) if native_started is None else native_started
        shadow_act = (shadow_portfolio is not None) if shadow_started is None else shadow_started
        r2_act = (
            (r2_result is not None or (self.r2_telemetry_path is not None and self.r2_telemetry_path.exists()))
            if r2_started is None
            else r2_started
        )

        sys_a = self.build_system_a_native_metrics(
            native_portfolio=native_portfolio,
            now_dt=now_dt,
            is_started=native_act,
            cohort_id=effective_cohort,
            t0_utc=effective_t0,
        )
        sys_b = self.build_system_b_shadow_metrics(
            shadow_portfolio=shadow_portfolio,
            now_dt=now_dt,
            is_started=shadow_act,
            cohort_id=effective_cohort,
            t0_utc=effective_t0,
        )
        sys_c = self.build_system_c_r2_metrics(
            r2_result=r2_result,
            now_dt=now_dt,
            is_started=r2_act,
        )

        all_started = native_act and shadow_act and r2_act
        cohort_valid = (
            sys_a.cohort_id == effective_cohort
            and sys_b.cohort_id == effective_cohort
            and sys_c.cohort_id == effective_cohort
        )

        return {
            "title": "TORG-BOT Three-System Comparative Dashboard",
            "protocol": "TORG-BOT — THREE-SYSTEM-LAUNCH-GATE-006",
            "timestamp_iso": (now_dt or datetime.now(timezone.utc)).isoformat(),
            "target_cohort": {
                "cohort_id": effective_cohort,
                "t0_utc": effective_t0,
                "initial_capital_per_system_usdt": 300.0,
                "total_independent_systems": 3,
                "all_systems_started": all_started,
                "cohort_status": "VALID_SYNCHRONIZED" if cohort_valid else "COHORT_MISMATCH",
            },
            "systems": {
                "native": sys_a.__dict__,
                "shadow": sys_b.__dict__,
                "r2_shadow": sys_c.__dict__,
            },
            "separation_rules": {
                "spot_perp_separation_enforced": True,
                "portfolio_pooling_forbidden": True,
                "original_v32_port_8083_untouched": True,
            },
        }

    def render_html_dashboard(self, report: Dict[str, Any]) -> str:
        """Renders responsive, readable HTML comparative dashboard."""
        tc = report.get("target_cohort", {})
        sys_map = report.get("systems", {})
        a = sys_map.get("native", {})
        b = sys_map.get("shadow", {})
        c = sys_map.get("r2_shadow", {})

        def fmt_val(v, suffix=""):
            return f"{v:,.2f}{suffix}" if v is not None else "<span class='dim'>--</span>"

        def fmt_pct(v):
            if v is None:
                return "<span class='dim'>--</span>"
            color = "#00e676" if v > 0 else ("#ff5252" if v < 0 else "#ffffff")
            sign = "+" if v > 0 else ""
            return f"<span style='color:{color}'>{sign}{v:.2f}%</span>"

        def fmt_status(st):
            colors = {
                "FRESH": "#00e676",
                "ARMED": "#00e676",
                "OBSERVING": "#00e676",
                "STALE": "#ffa726",
                "NOT_STARTED": "#78909c",
                "NO_FRESH_HEARTBEAT": "#ff5252",
                "INVALID": "#ff5252",
                "COHORT_MISMATCH": "#ff1744",
            }
            c_code = colors.get(st, "#b0bec5")
            return f"<span class='badge' style='background:{c_code}22; color:{c_code}; border:1px solid {c_code}'>{st}</span>"

        r2_extra = c.get("extra", {})
        edge_usdt = r2_extra.get("r2_minus_t01_usdt")
        edge_pp = r2_extra.get("r2_minus_t01_pp")
        edge_str = f"{fmt_val(edge_usdt, ' USDT')} ({fmt_pct(edge_pp)})" if edge_usdt is not None else "<span class='dim'>--</span>"

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TORG-BOT Three-System Comparative Dashboard</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background: #0b0e14; color: #e0e6ed; margin: 0; padding: 24px; }}
  h1 {{ margin: 0 0 8px 0; font-size: 24px; color: #64b5f6; font-weight: 600; }}
  .header-meta {{ font-size: 13px; color: #90a4ae; margin-bottom: 24px; }}
  .banner {{ background: #151c28; border: 1px solid #233142; border-radius: 8px; padding: 12px 16px; margin-bottom: 20px; font-size: 13px; }}
  .grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; }}
  .card {{ background: #121824; border: 1px solid #1f2a3a; border-radius: 8px; padding: 18px; }}
  .card-title {{ font-size: 18px; font-weight: 600; margin: 0 0 4px 0; }}
  .card-sub {{ font-size: 12px; color: #90a4ae; margin-bottom: 16px; min-height: 32px; }}
  .metric-row {{ display: flex; justify-content: space-between; padding: 8px 0; border-bottom: 1px solid #1c2636; font-size: 13px; }}
  .metric-row:last-child {{ border-bottom: none; }}
  .label {{ color: #78909c; }}
  .val {{ font-weight: 500; }}
  .dim {{ color: #546e7a; }}
  .badge {{ padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; text-transform: uppercase; }}
  .footer {{ margin-top: 24px; font-size: 12px; color: #546e7a; border-top: 1px solid #1c2636; padding-top: 12px; }}
</style>
</head>
<body>
  <h1>TORG-BOT Three-System Comparative Dashboard</h1>
  <div class="header-meta">
    Cohort: <strong>{tc.get('cohort_id')}</strong> | Scheduled T0: <strong>{tc.get('t0_utc')}</strong> | Status: {fmt_status(tc.get('cohort_status'))}
  </div>
  <div class="banner">
    <strong>Strict Separation Invariant Enforced:</strong> Bybit Linear Perpetuals (Systems A & B) and Spot Cash (System C) are physically isolated. Under no condition may Spot and Perpetual PnL be pooled. Port 8083 (historical v3.2) remains active and untouched.
  </div>
  <div class="grid">
    <!-- System A -->
    <div class="card">
      <div class="card-title" style="color:#4fc3f7">{a.get('name')}</div>
      <div class="card-sub">{a.get('role')}</div>
      <div class="metric-row"><span class="label">Market</span><span class="val">Bybit Linear Perp</span></div>
      <div class="metric-row"><span class="label">Status</span><span class="val">{fmt_status(a.get('data_freshness_status'))}</span></div>
      <div class="metric-row"><span class="label">Capital / Equity</span><span class="val">${fmt_val(a.get('initial_capital_usdt'))} / ${fmt_val(a.get('equity_usdt'))}</span></div>
      <div class="metric-row"><span class="label">Net PnL / Return</span><span class="val">${fmt_val(a.get('net_pnl_usdt'))} ({fmt_pct(a.get('net_return_pct'))})</span></div>
      <div class="metric-row"><span class="label">Max Drawdown</span><span class="val">{fmt_pct(a.get('mdd_pct'))}</span></div>
      <div class="metric-row"><span class="label">Cash Margin</span><span class="val">${fmt_val(a.get('cash_usdt'))}</span></div>
      <div class="metric-row"><span class="label">Exposure</span><span class="val">{fmt_val(a.get('exposure_pct'), '%')}</span></div>
      <div class="metric-row"><span class="label">Turnover</span><span class="val">${fmt_val(a.get('turnover_usdt'))}</span></div>
      <div class="metric-row"><span class="label">Fees / Funding</span><span class="val">${fmt_val(a.get('fees_paid_usdt'))} / ${fmt_val(a.get('funding_paid_usdt'))}</span></div>
      <div class="metric-row"><span class="label">Active Positions</span><span class="val">{a.get('positions_count', 0)}</span></div>
      <div class="metric-row"><span class="label">T0 / Cohort</span><span class="val">{a.get('cohort_id')}</span></div>
    </div>
    <!-- System B -->
    <div class="card">
      <div class="card-title" style="color:#ab47bc">{b.get('name')}</div>
      <div class="card-sub">{b.get('role')}</div>
      <div class="metric-row"><span class="label">Market</span><span class="val">Bybit Linear Perp</span></div>
      <div class="metric-row"><span class="label">Status</span><span class="val">{fmt_status(b.get('data_freshness_status'))}</span></div>
      <div class="metric-row"><span class="label">Capital / Equity</span><span class="val">${fmt_val(b.get('initial_capital_usdt'))} / ${fmt_val(b.get('equity_usdt'))}</span></div>
      <div class="metric-row"><span class="label">Net PnL / Return</span><span class="val">${fmt_val(b.get('net_pnl_usdt'))} ({fmt_pct(b.get('net_return_pct'))})</span></div>
      <div class="metric-row"><span class="label">Max Drawdown</span><span class="val">{fmt_pct(b.get('mdd_pct'))}</span></div>
      <div class="metric-row"><span class="label">Cash Margin</span><span class="val">${fmt_val(b.get('cash_usdt'))}</span></div>
      <div class="metric-row"><span class="label">Exposure</span><span class="val">{fmt_val(b.get('exposure_pct'), '%')}</span></div>
      <div class="metric-row"><span class="label">Turnover</span><span class="val">${fmt_val(b.get('turnover_usdt'))}</span></div>
      <div class="metric-row"><span class="label">Fees / Funding</span><span class="val">${fmt_val(b.get('fees_paid_usdt'))} / ${fmt_val(b.get('funding_paid_usdt'))}</span></div>
      <div class="metric-row"><span class="label">Active Positions</span><span class="val">{b.get('positions_count', 0)}</span></div>
      <div class="metric-row"><span class="label">T0 / Cohort</span><span class="val">{b.get('cohort_id')}</span></div>
    </div>
    <!-- System C -->
    <div class="card">
      <div class="card-title" style="color:#26a69a">{c.get('name')}</div>
      <div class="card-sub">{c.get('role')}</div>
      <div class="metric-row"><span class="label">Market</span><span class="val">Bybit Spot Long-Only</span></div>
      <div class="metric-row"><span class="label">Status</span><span class="val">{fmt_status(c.get('data_freshness_status'))}</span></div>
      <div class="metric-row"><span class="label">Capital / Equity</span><span class="val">${fmt_val(c.get('initial_capital_usdt'))} / ${fmt_val(c.get('equity_usdt'))}</span></div>
      <div class="metric-row"><span class="label">Net PnL / Return</span><span class="val">${fmt_val(c.get('net_pnl_usdt'))} ({fmt_pct(c.get('net_return_pct'))})</span></div>
      <div class="metric-row"><span class="label">Max Drawdown</span><span class="val">{fmt_pct(c.get('mdd_pct'))}</span></div>
      <div class="metric-row"><span class="label">Free Cash</span><span class="val">${fmt_val(c.get('cash_usdt'))}</span></div>
      <div class="metric-row"><span class="label">Exposure</span><span class="val">{fmt_val(c.get('exposure_pct'), '%')}</span></div>
      <div class="metric-row"><span class="label">Turnover</span><span class="val">${fmt_val(c.get('turnover_usdt'))}</span></div>
      <div class="metric-row"><span class="label">Fees / Spread</span><span class="val">${fmt_val(c.get('fees_paid_usdt'))} / ${fmt_val(c.get('slippage_cost_usdt'))}</span></div>
      <div class="metric-row"><span class="label">Edge over T01</span><span class="val">{edge_str}</span></div>
      <div class="metric-row"><span class="label">T0 / Cohort</span><span class="val">{c.get('cohort_id')}</span></div>
    </div>
  </div>
  <div class="footer">
    TORG-BOT 3-System Dashboard | Strictly Read-Only | Generated at {report.get('timestamp_iso')}
  </div>
</body>
</html>"""
        return html


class ThreeSystemDashboardHTTPHandler(BaseHTTPRequestHandler):
    """Read-only HTTP handler serving HTML and REST JSON telemetry."""
    
    dashboard_provider: Optional[ThreeSystemComparativeDashboard] = None
    report_supplier: Optional[Any] = None

    def log_message(self, format, *args):
        # Quiet logger
        pass

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            report = self._get_report()
            html = self.dashboard_provider.render_html_dashboard(report)
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in ["/status", "/api/status", "/telemetry", "/api/telemetry"]:
            report = self._get_report()
            body = json.dumps(report, indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif self.path == "/health":
            body = json.dumps({"status": "ok", "read_only": True, "service": "three_system_dashboard"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        # Strict read-only enforcement
        self.send_response(405)
        self.end_headers()
        self.wfile.write(b"405 Method Not Allowed (Read-Only Interface)\n")

    def do_PUT(self):
        self.do_POST()

    def do_DELETE(self):
        self.do_POST()

    def _get_report(self) -> Dict[str, Any]:
        if callable(self.report_supplier):
            return self.report_supplier()
        if self.dashboard_provider:
            return self.dashboard_provider.generate_comparative_report()
        return {"error": "Dashboard provider not attached"}


def create_dashboard_server(
    host: str = "127.0.0.1",
    port: int = 8099,
    dashboard_provider: Optional[ThreeSystemComparativeDashboard] = None,
    report_supplier: Optional[Any] = None,
) -> HTTPServer:
    """Creates a ThreadingHTTPServer with SO_REUSEADDR."""
    handler_cls = ThreeSystemDashboardHTTPHandler
    handler_cls.dashboard_provider = dashboard_provider or ThreeSystemComparativeDashboard()
    handler_cls.report_supplier = report_supplier

    # Enforce SO_REUSEADDR on HTTPServer
    class ReusableHTTPServer(HTTPServer):
        allow_reuse_address = True

    return ReusableHTTPServer((host, port), handler_cls)
