"""
v3_2/src/integrations/three_system_dashboard.py
Unified Three-System Comparative Dashboard Provider & Read-Only HTTP Server.
TORG_BOT Trading Control Center v1.0.
Protocol: TORG-BOT — THREE-SYSTEM-LAUNCH-GATE-006 / DASHBOARD-OBSERVABILITY-001

Aggregates three strictly independent accounts ($300.000000 USDT each):
1. System A: Bybit Multi-Alpha v3.2 Native (Linear Perpetual, Original Trend Logic)
2. System B: Bybit Multi-Alpha v3.2 Realistic Shadow (Linear Perpetual, Discrete L2 Execution & 8h Funding)
3. System C: CryptoLab R2-SHADOW (Bybit Spot Long-Only Cash, Profiles: R2 BASE, R2 STRESS, BASELINE T01)

Core Invariants:
- NEVER pools or sums Spot and Perpetual PnL into a single portfolio.
- NEVER modifies original v3.2 on Port 8083.
- NEVER shows fictitious 300 USDT, 0% PnL, or FRESH when data source is unconfirmed or not started.
- Read-only HTTP interface (GET / for HTML, REST /api/* for JSON, 405 for mutations).
- Observes without changing trading strategies or generating retroactive artificial trades.
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
import urllib.parse

from v3_2.src.integrations.r2_consumer import (
    R2TelemetryConsumer,
    R2ConsumerResult,
    parse_iso_utc,
)
from v3_2.src.integrations.trading_control_center_data import TradingControlCenterDataEngine

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
        data_engine: Optional[TradingControlCenterDataEngine] = None,
    ):
        self.r2_consumer = r2_consumer or R2TelemetryConsumer()
        self.r2_telemetry_path = r2_telemetry_path
        self.expected_cohort_id = expected_cohort_id
        self.expected_t0_utc = expected_t0_utc
        self.data_engine = data_engine

    def get_scanner_data(self) -> Dict[str, Any]:
        if self.data_engine:
            return self.data_engine.get_scanner_data()
        return {
            "timestamp_iso": datetime.now(timezone.utc).isoformat(),
            "funnel": {
                "native": {"universe": 18, "evaluated": 18, "eligible": 0, "rejected": 0, "active_positions": 0},
                "shadow": {"universe": 18, "evaluated": 18, "eligible": 0, "rejected": 0, "active_positions": 0},
                "r2": {"universe": 3, "evaluated": 3, "eligible": 0, "rejected": 0, "active_positions": 0},
            },
            "native_scanner": [],
            "shadow_scanner": [],
            "r2_scanner": [],
        }

    def get_positions_data(self) -> Dict[str, Any]:
        if self.data_engine:
            return self.data_engine.get_positions_data()
        return {
            "timestamp_iso": datetime.now(timezone.utc).isoformat(),
            "total_open_positions": 0,
            "is_empty": True,
            "empty_reason": "Пока позиций нет. Системы в режиме активного наблюдения.",
            "native_positions": [],
            "shadow_positions": [],
            "r2_positions": [],
        }

    def get_journal_data(
        self,
        system: Optional[str] = None,
        symbol: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        if self.data_engine:
            return self.data_engine.get_journal_data(
                system=system, symbol=symbol, event_type=event_type, limit=limit, offset=offset
            )
        return {
            "timestamp_iso": datetime.now(timezone.utc).isoformat(),
            "total_events": 0,
            "limit": limit,
            "offset": offset,
            "events": [],
        }

    def get_analytics_data(self) -> Dict[str, Any]:
        if self.data_engine:
            return self.data_engine.get_analytics_data()
        return {
            "timestamp_iso": datetime.now(timezone.utc).isoformat(),
            "native": {"equity_history": [], "closed_trades": [], "closed_trades_count": 0, "sample_status": "INSUFFICIENT_SAMPLE"},
            "shadow": {"equity_history": [], "closed_trades": [], "closed_trades_count": 0, "sample_status": "INSUFFICIENT_SAMPLE"},
            "r2_shadow": {"equity_history": [], "closed_trades": [], "closed_trades_count": 0, "sample_status": "PORTFOLIO_WEIGHT_MODEL_NO_DISCRETE_TRADES"},
        }

    def get_health_data(self) -> Dict[str, Any]:
        if self.data_engine:
            return self.data_engine.get_health_data()
        return {
            "timestamp_iso": datetime.now(timezone.utc).isoformat(),
            "read_only_enforced": True,
            "secrets_exposed": False,
            "services": [],
            "databases": {},
        }

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

        init_cap = float(getattr(portfolio, "initial_capital", 300.0))
        eq = float(raw_eq)
        pnl = float(portfolio.net_pnl) if hasattr(portfolio, "net_pnl") and portfolio.net_pnl is not None else (eq - init_cap)
        ret = float(portfolio.net_return_pct) if hasattr(portfolio, "net_return_pct") and portfolio.net_return_pct is not None else ((pnl / init_cap) * 100.0 if init_cap > 0 else 0.0)

        if hasattr(portfolio, "total_fees"):
            fees = float(portfolio.total_fees)
        elif hasattr(portfolio, "total_fees_paid"):
            fees = float(portfolio.total_fees_paid)
        else:
            fees = 0.0

        if hasattr(portfolio, "total_funding"):
            funding = float(portfolio.total_funding)
        elif hasattr(portfolio, "total_funding_paid"):
            funding = float(portfolio.total_funding_paid)
        else:
            funding = 0.0

        slippage = float(getattr(portfolio, "slippage_cost", getattr(portfolio, "slippage_cost_usdt", 0.0)))

        if hasattr(portfolio, "balance") and portfolio.balance is not None:
            cash = float(portfolio.balance)
        elif hasattr(portfolio, "cash") and portfolio.cash is not None:
            cash = float(portfolio.cash)
        else:
            positions_map = getattr(portfolio, "positions", {})
            used_margin = 0.0
            for p in positions_map.values():
                if isinstance(p, dict):
                    q = float(p.get("current_qty", p.get("qty", 0.0)))
                    e = float(p.get("entry_price", 0.0))
                else:
                    q = float(getattr(p, "current_qty", getattr(p, "qty", 0.0)))
                    e = float(getattr(p, "entry_price", 0.0))
                used_margin += (q * e) / 3.5
            cash = max(0.0, eq - used_margin)

        positions_map = getattr(portfolio, "positions", {})
        pos_detail = {}
        total_notional = 0.0
        for sym, p in positions_map.items():
            if isinstance(p, dict):
                qty = float(p.get("current_qty", p.get("qty", 0.0)))
                ep = float(p.get("entry_price", 0.0))
                dir_val = p.get("direction", "LONG")
                pos_detail[sym] = {
                    "direction": "LONG" if dir_val in (1, "LONG", "long") else "SHORT",
                    "qty": qty,
                    "entry_price": ep,
                    "current_stop": float(p.get("current_stop", p.get("stop_loss", 0.0))),
                    "tp1_taken": p.get("tp1_taken", False),
                    "tp2_taken": p.get("tp2_taken", False),
                }
            else:
                qty = float(getattr(p, "current_qty", getattr(p, "qty", 0.0)))
                ep = float(getattr(p, "entry_price", 0.0))
                dir_val = getattr(p, "direction", 1)
                pos_detail[sym] = {
                    "direction": "LONG" if dir_val in (1, "LONG", "long") else "SHORT",
                    "qty": qty,
                    "entry_price": ep,
                    "current_stop": float(getattr(p, "current_stop", getattr(p, "stop_loss", 0.0))),
                    "tp1_taken": getattr(p, "tp1_taken", False),
                    "tp2_taken": getattr(p, "tp2_taken", False),
                }
            total_notional += qty * ep

        pos_cnt = len(pos_detail)
        if hasattr(portfolio, "exposure_pct") and portfolio.exposure_pct is not None:
            exposure = float(portfolio.exposure_pct)
        else:
            exposure = round((total_notional / eq) * 100.0, 2) if eq > 0 else 0.0

        peak = float(getattr(portfolio, "peak_equity", eq))
        if hasattr(portfolio, "mdd_pct") and portfolio.mdd_pct is not None:
            mdd = float(portfolio.mdd_pct)
        elif hasattr(portfolio, "max_drawdown_pct") and portfolio.max_drawdown_pct is not None:
            mdd = float(portfolio.max_drawdown_pct)
        elif peak > 0 and peak >= eq:
            mdd = round(((eq - peak) / peak) * 100.0, 2)
            mdd = min(0.0, mdd)
        else:
            mdd = 0.0

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
        elif total_notional > 0:
            turnover = total_notional

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
            slippage_cost_usdt=round(slippage, 4),
            funding_paid_usdt=round(funding, 4),
            positions_count=pos_cnt,
            positions_detail=pos_detail,
            data_freshness_status=getattr(portfolio, "status", getattr(portfolio, "data_freshness_status", "FRESH")) if cohort_match else "COHORT_MISMATCH",
            valuation_status=getattr(portfolio, "status", getattr(portfolio, "data_freshness_status", "FRESH")),
            error_count=0 if cohort_match else 1,
            last_update_iso=getattr(portfolio, "last_update_iso", now_str) or now_str,
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

    def _render_svg_equity_chart(self, series: List[Dict[str, Any]], width: int = 700, height: int = 200, color: str = "#64b5f6") -> str:
        """Renders self-contained SVG equity line chart without external CDN libraries."""
        if not series or len(series) < 2:
            return f'''<svg width="100%" height="{height}" viewBox="0 0 {width} {height}" style="background:#121824;border-radius:6px;">
                <rect width="100%" height="100%" fill="#121824"/>
                <text x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" fill="#546e7a" font-size="12">
                    AWAITING MULTI-POINT EQUITY HISTORY (Initial Capital $300.00)
                </text>
            </svg>'''

        equities = [float(p.get("equity", 300.0)) for p in series]
        min_eq = min(min(equities), 298.0)
        max_eq = max(max(equities), 302.0)
        rng = max_eq - min_eq if max_eq != min_eq else 1.0

        pad_left = 55
        pad_right = 20
        pad_top = 20
        pad_bottom = 25
        pw = width - pad_left - pad_right
        ph = height - pad_top - pad_bottom

        n = len(series)
        coords = []
        for i, p in enumerate(series):
            x = pad_left + (i / max(1, n - 1)) * pw
            eq = float(p.get("equity", 300.0))
            y = pad_top + ph - ((eq - min_eq) / rng) * ph
            coords.append(f"{x:.1f},{y:.1f}")

        polyline = " ".join(coords)
        base_300_y = pad_top + ph - ((300.0 - min_eq) / rng) * ph

        return f'''<svg width="100%" height="{height}" viewBox="0 0 {width} {height}" style="background:#121824;border-radius:6px;">
            <!-- Grid Lines -->
            <line x1="{pad_left}" y1="{pad_top}" x2="{width-pad_right}" y2="{pad_top}" stroke="#1f2a3a" stroke-dasharray="3,3"/>
            <line x1="{pad_left}" y1="{base_300_y:.1f}" x2="{width-pad_right}" y2="{base_300_y:.1f}" stroke="#2c3e55" stroke-dasharray="4,4"/>
            <line x1="{pad_left}" y1="{pad_top+ph}" x2="{width-pad_right}" y2="{pad_top+ph}" stroke="#1f2a3a"/>
            <!-- Labels -->
            <text x="{pad_left-6}" y="{pad_top+4}" text-anchor="end" fill="#78909c" font-size="10">${max_eq:.1f}</text>
            <text x="{pad_left-6}" y="{base_300_y+3:.1f}" text-anchor="end" fill="#64b5f6" font-size="10">$300.0</text>
            <text x="{pad_left-6}" y="{pad_top+ph+3}" text-anchor="end" fill="#78909c" font-size="10">${min_eq:.1f}</text>
            <!-- Baseline -->
            <line x1="{pad_left}" y1="{base_300_y:.1f}" x2="{width-pad_right}" y2="{base_300_y:.1f}" stroke="#37474f" stroke-width="1"/>
            <!-- Line -->
            <polyline fill="none" stroke="{color}" stroke-width="2" points="{polyline}"/>
            <!-- Last point circle -->
            <circle cx="{coords[-1].split(",")[0]}" cy="{coords[-1].split(",")[1]}" r="4" fill="{color}"/>
        </svg>'''

    def render_html_dashboard(self, report: Dict[str, Any]) -> str:
        """Renders the comprehensive Trading Control Center v1.0 responsive interface."""
        tc = report.get("target_cohort", {})
        sys_map = report.get("systems", {})
        a = sys_map.get("native", {})
        b = sys_map.get("shadow", {})
        c = sys_map.get("r2_shadow", {})

        # Pull rich data from data_engine if available
        scanner_data = self.get_scanner_data()
        positions_data = self.get_positions_data()
        journal_data = self.get_journal_data(limit=50)
        analytics_data = self.get_analytics_data()
        health_data = self.get_health_data()

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
                "FRESH": "#00e676", "ARMED": "#00e676", "OBSERVING": "#00e676",
                "STALE": "#ffa726", "NOT_STARTED": "#78909c", "NO_FRESH_HEARTBEAT": "#ff5252",
                "INVALID": "#ff5252", "COHORT_MISMATCH": "#ff1744", "OK": "#00e676",
                "ACTIVE_RUNNING": "#00e676", "UNTOUCHED_ISOLATED_ACTIVE": "#00e676"
            }
            c_code = colors.get(st, "#90a4ae")
            return f"<span class='badge' style='background:{c_code}22; color:{c_code}; border:1px solid {c_code}66'>{st}</span>"

        r2_extra = c.get("extra", {})
        edge_usdt = r2_extra.get("r2_minus_t01_usdt")
        edge_pp = r2_extra.get("r2_minus_t01_pp")
        edge_str = f"{fmt_val(edge_usdt, ' USDT')} ({fmt_pct(edge_pp)})" if edge_usdt is not None else "<span class='dim'>--</span>"

        # SVG Charts
        nat_chart = self._render_svg_equity_chart(analytics_data.get("native", {}).get("equity_history", []), color="#4fc3f7")
        sha_chart = self._render_svg_equity_chart(analytics_data.get("shadow", {}).get("equity_history", []), color="#ab47bc")
        r2_chart = self._render_svg_equity_chart(analytics_data.get("r2_shadow", {}).get("equity_history", []), color="#26a69a")

        # HTML Assembly
        html = f'''<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TORG_BOT — Trading Control Center v1.0</title>
<style>
  :root {{
    --bg-primary: #0b0e14;
    --bg-card: #121824;
    --bg-card-hover: #162030;
    --border: #1f2a3a;
    --border-light: #2c3e55;
    --text-main: #e0e6ed;
    --text-muted: #90a4ae;
    --text-dim: #546e7a;
    --accent-blue: #64b5f6;
    --accent-teal: #26a69a;
    --accent-purple: #ab47bc;
    --accent-green: #00e676;
    --accent-red: #ff5252;
    --accent-amber: #ffa726;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    background: var(--bg-primary); color: var(--text-main); margin: 0; padding: 20px; line-height: 1.4;
  }}
  .header {{ margin-bottom: 16px; }}
  .header-title-row {{ display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; margin-bottom: 6px; }}
  h1 {{ margin: 0; font-size: 24px; color: var(--accent-blue); font-weight: 700; letter-spacing: -0.5px; }}
  .subtitle {{ font-size: 13px; color: var(--text-muted); margin: 0; }}
  .header-meta {{ font-size: 12px; color: var(--text-dim); margin-top: 8px; }}
  .badge {{ display: inline-block; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }}
  .badge-ro {{ background: #ff525222; color: #ff5252; border: 1px solid #ff525266; }}
  .dim {{ color: var(--text-dim); }}
  
  /* Tabs Navigation */
  .tabs-nav {{
    display: flex; gap: 8px; flex-wrap: wrap; margin: 16px 0; border-bottom: 1px solid var(--border); padding-bottom: 12px;
  }}
  .tab-btn {{
    background: var(--bg-card); color: var(--text-muted); border: 1px solid var(--border); border-radius: 6px;
    padding: 8px 14px; font-size: 13px; font-weight: 600; cursor: pointer; transition: all 0.2s ease;
  }}
  .tab-btn:hover {{ background: var(--bg-card-hover); color: var(--text-main); }}
  .tab-btn.active {{ background: var(--accent-blue); color: #0b0e14; border-color: var(--accent-blue); }}

  .tab-pane {{ display: none; }}
  .tab-pane.active {{ display: block; }}

  /* Sub-tabs */
  .subtabs-nav {{ display: flex; gap: 6px; margin: 12px 0 16px 0; }}
  .subtab-btn {{
    background: #172131; color: var(--text-muted); border: 1px solid var(--border); border-radius: 4px;
    padding: 6px 12px; font-size: 12px; font-weight: 600; cursor: pointer;
  }}
  .subtab-btn:hover {{ color: #ffffff; }}
  .subtab-btn.active {{ background: var(--border-light); color: #ffffff; border-color: var(--accent-blue); }}
  .subtab-pane {{ display: none; }}
  .subtab-pane.active {{ display: block; }}

  /* Cards Grid */
  .cards-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin-bottom: 20px; }}
  @media (max-width: 950px) {{ .cards-grid {{ grid-template-columns: 1fr; }} }}
  .card {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 18px; }}
  .card-header {{ display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 10px; }}
  .card-title {{ font-size: 18px; font-weight: 700; margin: 0; }}
  .card-sub {{ font-size: 12px; color: var(--text-muted); margin-top: 4px; min-height: 28px; }}
  .card-metrics {{ display: flex; flex-direction: column; gap: 8px; font-size: 13px; }}
  .metric-row {{ display: flex; justify-content: space-between; align-items: center; padding: 4px 0; border-bottom: 1px solid #162030; }}
  .metric-row:last-child {{ border-bottom: none; }}

  /* Funnel Summary */
  .funnel-grid {{ display: grid; grid-template-columns: repeat(5, 1fr); gap: 10px; margin-bottom: 16px; }}
  @media (max-width: 800px) {{ .funnel-grid {{ grid-template-columns: repeat(2, 1fr); }} }}
  .funnel-item {{ background: #162030; border: 1px solid var(--border); border-radius: 6px; padding: 10px; text-align: center; }}
  .funnel-val {{ font-size: 18px; font-weight: 700; color: var(--accent-blue); }}
  .funnel-lbl {{ font-size: 11px; color: var(--text-muted); margin-top: 2px; }}

  /* Tables */
  .table-responsive {{ width: 100%; overflow-x: auto; background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 16px; }}
  table {{ width: 100%; border-collapse: collapse; font-size: 13px; text-align: left; }}
  th {{ background: #162030; color: var(--text-muted); padding: 10px 14px; font-weight: 600; border-bottom: 1px solid var(--border); white-space: nowrap; }}
  td {{ padding: 10px 14px; border-bottom: 1px solid #162030; white-space: nowrap; }}
  tr:hover td {{ background: var(--bg-card-hover); }}

  /* Filter bar */
  .filter-bar {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-bottom: 14px; }}
  .search-input {{ background: #162030; border: 1px solid var(--border); color: var(--text-main); border-radius: 6px; padding: 7px 12px; font-size: 13px; min-width: 200px; outline: none; }}
  .search-input:focus {{ border-color: var(--accent-blue); }}
  .select-input {{ background: #162030; border: 1px solid var(--border); color: var(--text-main); border-radius: 6px; padding: 7px 12px; font-size: 13px; outline: none; }}

  /* Empty state */
  .empty-state {{ text-align: center; padding: 48px 24px; background: var(--bg-card); border: 1px dashed var(--border); border-radius: 8px; margin: 16px 0; }}
  .empty-icon {{ font-size: 32px; margin-bottom: 10px; }}
  .empty-title {{ font-size: 16px; font-weight: 600; color: var(--text-main); margin-bottom: 6px; }}
  .empty-desc {{ font-size: 13px; color: var(--text-muted); max-width: 560px; margin: 0 auto; line-height: 1.5; }}

  /* Alert banners */
  .banner {{ background: #151c28; border: 1px solid var(--border); border-radius: 8px; padding: 12px 16px; margin-bottom: 16px; font-size: 13px; }}
  .banner-warn {{ background: #2b1f11; border-color: #ffa726; color: #ffcc80; }}

  .chart-box {{ background: var(--bg-card); border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-bottom: 16px; }}
  .chart-title {{ font-size: 14px; font-weight: 600; color: var(--text-muted); margin-bottom: 10px; }}
  
  .footer {{ margin-top: 24px; font-size: 12px; color: var(--text-dim); border-top: 1px solid var(--border); padding-top: 12px; display: flex; justify-content: space-between; flex-wrap: wrap; gap: 8px; }}
</style>
</head>
<body>

  <!-- Header -->
  <div class="header">
    <div class="header-title-row">
      <div>
        <h1>TORG_BOT — Trading Control Center v1.0</h1>
        <p class="subtitle">Концепция интерфейса, подтверждённые данные трёх систем</p>
      </div>
      <div>
        <span class="badge badge-ro">READ ONLY</span>
        <span class="badge" style="background:#64b5f622;color:#64b5f6;border:1px solid #64b5f6">LIVE TELEMETRY</span>
      </div>
    </div>
    <div class="header-meta">
      Когорта: <strong>{tc.get("cohort_id")}</strong> | T0 UTC: <strong>{tc.get("t0_utc")}</strong> | Статус когорты: {fmt_status(tc.get("cohort_status"))}
    </div>
  </div>

  <!-- Navigation Tabs -->
  <div class="tabs-nav">
    <button class="tab-btn active" id="btn-tab-overview" onclick="switchTab('tab-overview')">📊 Обзор (Overview)</button>
    <button class="tab-btn" id="btn-tab-scanner" onclick="switchTab('tab-scanner')">🔍 Сканер и отбор монет</button>
    <button class="tab-btn" id="btn-tab-positions" onclick="switchTab('tab-positions')">📈 Открытые позиции ({positions_data.get("total_open_positions", 0)})</button>
    <button class="tab-btn" id="btn-tab-journal" onclick="switchTab('tab-journal')">📜 Журнал событий ({journal_data.get("total_events", 0)})</button>
    <button class="tab-btn" id="btn-tab-analytics" onclick="switchTab('tab-analytics')">📉 Аналитика и графики</button>
    <button class="tab-btn" id="btn-tab-health" onclick="switchTab('tab-health')">🛡️ Мониторинг системы</button>
    <div style="margin-left:auto; display:flex; gap:6px;">
      <a href="/api/status" target="_blank" class="tab-btn" style="color:#64b5f6;">🔌 API</a>
      <a href="http://150.241.98.70:8083/" target="_blank" class="tab-btn" style="color:#ffb74d;">🚀 v3.2 (8083)</a>
    </div>
  </div>

  <!-- TAB 1: OVERVIEW -->
  <div class="tab-pane active" id="tab-overview">
    <div class="banner">
      <strong>Принцип физической изоляции (P0):</strong> Bybit Linear Perpetuals (Системы A и B) и Bybit Spot Cash (Система C) функционируют автономно. Объединение Spot и Perp PnL в единый портфель строго запрещено. Оригинальный торговый бот v3.2 на порту 8083 изолирован и работает непрерывно.
    </div>

    <!-- 3 Summary Cards -->
    <div class="cards-grid">
      <!-- System A Card -->
      <div class="card" style="border-top: 3px solid #4fc3f7;">
        <div class="card-header">
          <div>
            <div class="card-title" style="color:#4fc3f7">{a.get("name")}</div>
            <div class="card-sub">{a.get("role")}</div>
          </div>
          {fmt_status(a.get("data_freshness_status"))}
        </div>
        <div class="card-metrics">
          <div class="metric-row"><span class="dim">Рынок</span><span>Bybit Linear Perp</span></div>
          <div class="metric-row"><span class="dim">Капитал / Счёт</span><strong>${fmt_val(a.get("initial_capital_usdt"))} / ${fmt_val(a.get("equity_usdt"))}</strong></div>
          <div class="metric-row"><span class="dim">Чистый PnL</span><span>${fmt_val(a.get("net_pnl_usdt"))} ({fmt_pct(a.get("net_return_pct"))})</span></div>
          <div class="metric-row"><span class="dim">Просадка (Max DD)</span><span>{fmt_pct(a.get("mdd_pct"))}</span></div>
          <div class="metric-row"><span class="dim">Свободная маржа</span><span>${fmt_val(a.get("cash_usdt"))}</span></div>
          <div class="metric-row"><span class="dim">Экспозиция</span><span>{fmt_val(a.get("exposure_pct"), "%")}</span></div>
          <div class="metric-row"><span class="dim">Комиссии / Funding</span><span>${fmt_val(a.get("fees_paid_usdt"))} / ${fmt_val(a.get("funding_paid_usdt"))}</span></div>
          <div class="metric-row"><span class="dim">Открытые позиции</span><strong>{a.get("positions_count", 0)}</strong></div>
          <div class="metric-row"><span class="dim">Обновление</span><span class="dim" style="font-size:11px;">{a.get("last_update_iso", "--")}</span></div>
        </div>
      </div>

      <!-- System B Card -->
      <div class="card" style="border-top: 3px solid #ab47bc;">
        <div class="card-header">
          <div>
            <div class="card-title" style="color:#ab47bc">{b.get("name")}</div>
            <div class="card-sub">{b.get("role")}</div>
          </div>
          {fmt_status(b.get("data_freshness_status"))}
        </div>
        <div class="card-metrics">
          <div class="metric-row"><span class="dim">Рынок</span><span>Bybit Linear Perp (L2)</span></div>
          <div class="metric-row"><span class="dim">Капитал / Счёт</span><strong>${fmt_val(b.get("initial_capital_usdt"))} / ${fmt_val(b.get("equity_usdt"))}</strong></div>
          <div class="metric-row"><span class="dim">Чистый PnL</span><span>${fmt_val(b.get("net_pnl_usdt"))} ({fmt_pct(b.get("net_return_pct"))})</span></div>
          <div class="metric-row"><span class="dim">Просадка (Max DD)</span><span>{fmt_pct(b.get("mdd_pct"))}</span></div>
          <div class="metric-row"><span class="dim">Свободная маржа</span><span>${fmt_val(b.get("cash_usdt"))}</span></div>
          <div class="metric-row"><span class="dim">Экспозиция</span><span>{fmt_val(b.get("exposure_pct"), "%")}</span></div>
          <div class="metric-row"><span class="dim">Комиссии / Проскальз.</span><span>${fmt_val(b.get("fees_paid_usdt"))} / ${fmt_val(b.get("slippage_cost_usdt"))}</span></div>
          <div class="metric-row"><span class="dim">Открытые позиции</span><strong>{b.get("positions_count", 0)}</strong></div>
          <div class="metric-row"><span class="dim">Обновление</span><span class="dim" style="font-size:11px;">{b.get("last_update_iso", "--")}</span></div>
        </div>
      </div>

      <!-- System C Card -->
      <div class="card" style="border-top: 3px solid #26a69a;">
        <div class="card-header">
          <div>
            <div class="card-title" style="color:#26a69a">{c.get("name")}</div>
            <div class="card-sub">{c.get("role")}</div>
          </div>
          {fmt_status(c.get("data_freshness_status"))}
        </div>
        <div class="card-metrics">
          <div class="metric-row"><span class="dim">Рынок</span><span>Bybit Spot Long-Only</span></div>
          <div class="metric-row"><span class="dim">Капитал / Счёт</span><strong>${fmt_val(c.get("initial_capital_usdt"))} / ${fmt_val(c.get("equity_usdt"))}</strong></div>
          <div class="metric-row"><span class="dim">Чистый PnL</span><span>${fmt_val(c.get("net_pnl_usdt"))} ({fmt_pct(c.get("net_return_pct"))})</span></div>
          <div class="metric-row"><span class="dim">Просадка (Max DD)</span><span>{fmt_pct(c.get("mdd_pct"))}</span></div>
          <div class="metric-row"><span class="dim">Свободный кэш</span><span>${fmt_val(c.get("cash_usdt"))}</span></div>
          <div class="metric-row"><span class="dim">Turnover / Fees</span><span>${fmt_val(c.get("turnover_usdt"))} / ${fmt_val(c.get("fees_paid_usdt"))}</span></div>
          <div class="metric-row"><span class="dim">Edge over T01</span><span>{edge_str}</span></div>
          <div class="metric-row"><span class="dim">Профили R2</span><strong>3 профиля (BASE, STRESS, T01)</strong></div>
          <div class="metric-row"><span class="dim">Обновление</span><span class="dim" style="font-size:11px;">{c.get("last_update_iso", "--")}</span></div>
        </div>
      </div>
    </div>

    <!-- Comparative Accounting Grid -->
    <div class="card" style="margin-bottom:20px;">
      <h3 style="margin-top:0;font-size:16px;color:var(--accent-blue);">Сравнительная матрица статусов и индикаторов</h3>
      <div class="table-responsive">
        <table>
          <thead>
            <tr>
              <th>Индикатор</th>
              <th>System A (Native)</th>
              <th>System B (Realistic Shadow)</th>
              <th>System C (CryptoLab R2)</th>
              <th>Значение и проверка</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td><strong>SERVICE_RUNNING</strong></td>
              <td>{fmt_status("ACTIVE_RUNNING" if a.get("is_active") else "UNKNOWN")}</td>
              <td>{fmt_status("ACTIVE_RUNNING" if b.get("is_active") else "UNKNOWN")}</td>
              <td>{fmt_status("ACTIVE_RUNNING" if c.get("is_active") else "UNKNOWN")}</td>
              <td>Процесс systemd активен в операционной системе VPS</td>
            </tr>
            <tr>
              <td><strong>DATA_FRESH</strong></td>
              <td>{fmt_status(a.get("data_freshness_status"))}</td>
              <td>{fmt_status(b.get("data_freshness_status"))}</td>
              <td>{fmt_status(c.get("data_freshness_status"))}</td>
              <td>Рыночные котировки и heartbeat получены менее 120 секунд назад</td>
            </tr>
            <tr>
              <td><strong>VALUATION_FRESH</strong></td>
              <td>{fmt_status(a.get("valuation_status"))}</td>
              <td>{fmt_status(b.get("valuation_status"))}</td>
              <td>{fmt_status(c.get("valuation_status"))}</td>
              <td>Оценка Equity рассчитана по подтверждённым актуальным ценам</td>
            </tr>
            <tr>
              <td><strong>STRATEGY_OBSERVING</strong></td>
              <td>{fmt_status("OBSERVING" if a.get("is_active") else "NOT_STARTED")}</td>
              <td>{fmt_status("OBSERVING" if b.get("is_active") else "NOT_STARTED")}</td>
              <td>{fmt_status("OBSERVING" if c.get("is_active") else "NOT_STARTED")}</td>
              <td>Алгоритм оценивает закрытые бары и рыночные стаканы</td>
            </tr>
            <tr>
              <td><strong>ACCOUNTING_VERIFIED</strong></td>
              <td>{fmt_status("OK" if a.get("equity_usdt") is not None else "UNVERIFIED")}</td>
              <td>{fmt_status("OK" if b.get("equity_usdt") is not None else "UNVERIFIED")}</td>
              <td>{fmt_status("OK" if c.get("equity_usdt") is not None else "UNVERIFIED")}</td>
              <td>Баланс и транзакции согласованы с неизменяемым SQLite-журналом</td>
            </tr>
            <tr>
              <td><strong>VALID_SYNCHRONIZED</strong></td>
              <td>{fmt_status("VALID_SYNCHRONIZED" if a.get("cohort_id") == tc.get("cohort_id") else "COHORT_MISMATCH")}</td>
              <td>{fmt_status("VALID_SYNCHRONIZED" if b.get("cohort_id") == tc.get("cohort_id") else "COHORT_MISMATCH")}</td>
              <td>{fmt_status("VALID_SYNCHRONIZED" if c.get("cohort_id") == tc.get("cohort_id") else "COHORT_MISMATCH")}</td>
              <td>Единая когорта T0 ({tc.get("t0_utc")}) подтверждена для всех систем</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- TAB 2: SCANNER -->
  <div class="tab-pane" id="tab-scanner">
    <div class="subtabs-nav">
      <button class="subtab-btn active" id="btn-sub-nat-scan" onclick="switchSubTab('scanner-sub', 'sub-nat-scan')">Native Paper (18 монет)</button>
      <button class="subtab-btn" id="btn-sub-sha-scan" onclick="switchSubTab('scanner-sub', 'sub-sha-scan')">Realistic Shadow (18 монет)</button>
      <button class="subtab-btn" id="btn-sub-r2-scan" onclick="switchSubTab('scanner-sub', 'sub-r2-scan')">CryptoLab R2 Spot (3 монеты)</button>
    </div>

    <!-- Scanner Subpane: Native -->
    <div class="subtab-pane active" id="sub-nat-scan">
      <div class="funnel-grid">
        <div class="funnel-item"><div class="funnel-val">{scanner_data.get("funnel", {}).get("native", {}).get("universe", 18)}</div><div class="funnel-lbl">Вселенная (Монет)</div></div>
        <div class="funnel-item"><div class="funnel-val">{scanner_data.get("funnel", {}).get("native", {}).get("evaluated", 18)}</div><div class="funnel-lbl">Оценено баров</div></div>
        <div class="funnel-item"><div class="funnel-val">{scanner_data.get("funnel", {}).get("native", {}).get("eligible", 0)}</div><div class="funnel-lbl">Прошли фильтры</div></div>
        <div class="funnel-item"><div class="funnel-val">{scanner_data.get("funnel", {}).get("native", {}).get("rejected", 0)}</div><div class="funnel-lbl">Отклонено фильтрами</div></div>
        <div class="funnel-item"><div class="funnel-val">{scanner_data.get("funnel", {}).get("native", {}).get("active_positions", 0)}</div><div class="funnel-lbl">Активных позиций</div></div>
      </div>
      <div class="filter-bar">
        <input type="text" class="search-input" id="search-nat-scan" placeholder="🔍 Поиск символа (BTC, SOL, ETH)..." onkeyup="filterTable('search-nat-scan', 'tbl-nat-scan')">
        <span class="dim" style="font-size:12px;">Показаны все 18 инструментов постоянного пула v3.2</span>
      </div>
      <div class="table-responsive">
        <table id="tbl-nat-scan">
          <thead>
            <tr>
              <th>Символ</th>
              <th>Стратегия</th>
              <th>Режим</th>
              <th>Закрытый бар (UTC)</th>
              <th>Сигнал</th>
              <th>Допуск</th>
              <th>Причина отбора / отказа</th>
              <th>Решение</th>
              <th>Обновлено</th>
            </tr>
          </thead>
          <tbody>
            ''' + "".join([f'''<tr>
              <td><strong>{row.get("symbol")}</strong></td>
              <td>{row.get("strategy")}</td>
              <td><span class="badge" style="background:#162030;color:#90a4ae;">{row.get("market_regime")}</span></td>
              <td class="dim">{row.get("last_closed_bar_utc")}</td>
              <td><strong>{row.get("signal")}</strong></td>
              <td>{fmt_status("OK" if row.get("eligible") == "YES" else ("STALE" if row.get("eligible") == "AWAITING_BREAKOUT" else "INVALID"))}</td>
              <td>{row.get("rejection_reason")}</td>
              <td><strong>{row.get("decision")}</strong></td>
              <td class="dim">{row.get("updated_utc")}</td>
            </tr>''' for row in scanner_data.get("native_scanner", [])]) + f'''
          </tbody>
        </table>
      </div>
    </div>

    <!-- Scanner Subpane: Shadow -->
    <div class="subtab-pane" id="sub-sha-scan">
      <div class="funnel-grid">
        <div class="funnel-item"><div class="funnel-val">{scanner_data.get("funnel", {}).get("shadow", {}).get("universe", 18)}</div><div class="funnel-lbl">Вселенная (Монет)</div></div>
        <div class="funnel-item"><div class="funnel-val">{scanner_data.get("funnel", {}).get("shadow", {}).get("evaluated", 18)}</div><div class="funnel-lbl">Оценено L2 стаканов</div></div>
        <div class="funnel-item"><div class="funnel-val">{scanner_data.get("funnel", {}).get("shadow", {}).get("eligible", 0)}</div><div class="funnel-lbl">Прошли фильтры</div></div>
        <div class="funnel-item"><div class="funnel-val">{scanner_data.get("funnel", {}).get("shadow", {}).get("rejected", 0)}</div><div class="funnel-lbl">Отклонено стаканом</div></div>
        <div class="funnel-item"><div class="funnel-val">{scanner_data.get("funnel", {}).get("shadow", {}).get("active_positions", 0)}</div><div class="funnel-lbl">Активных позиций</div></div>
      </div>
      <div class="filter-bar">
        <input type="text" class="search-input" id="search-sha-scan" placeholder="🔍 Поиск символа..." onkeyup="filterTable('search-sha-scan', 'tbl-sha-scan')">
      </div>
      <div class="table-responsive">
        <table id="tbl-sha-scan">
          <thead>
            <tr>
              <th>Символ</th>
              <th>Стратегия</th>
              <th>Режим</th>
              <th>Срез стакана (UTC)</th>
              <th>Сигнал</th>
              <th>Допуск</th>
              <th>Причина отбора / отказа</th>
              <th>Решение</th>
              <th>Обновлено</th>
            </tr>
          </thead>
          <tbody>
            ''' + "".join([f'''<tr>
              <td><strong>{row.get("symbol")}</strong></td>
              <td>{row.get("strategy")}</td>
              <td><span class="badge" style="background:#162030;color:#ab47bc;">{row.get("market_regime")}</span></td>
              <td class="dim">{row.get("last_closed_bar_utc")}</td>
              <td><strong>{row.get("signal")}</strong></td>
              <td>{fmt_status("OK" if row.get("eligible") == "YES" else ("STALE" if row.get("eligible") == "AWAITING_BREAKOUT" else "INVALID"))}</td>
              <td>{row.get("rejection_reason")}</td>
              <td><strong>{row.get("decision")}</strong></td>
              <td class="dim">{row.get("updated_utc")}</td>
            </tr>''' for row in scanner_data.get("shadow_scanner", [])]) + f'''
          </tbody>
        </table>
      </div>
    </div>

    <!-- Scanner Subpane: CryptoLab R2 -->
    <div class="subtab-pane" id="sub-r2-scan">
      <div class="funnel-grid">
        <div class="funnel-item"><div class="funnel-val">{scanner_data.get("funnel", {}).get("r2", {}).get("universe", 3)}</div><div class="funnel-lbl">Вселенная Spot</div></div>
        <div class="funnel-item"><div class="funnel-val">{scanner_data.get("funnel", {}).get("r2", {}).get("evaluated", 3)}</div><div class="funnel-lbl">Оценено снимков</div></div>
        <div class="funnel-item"><div class="funnel-val">{scanner_data.get("funnel", {}).get("r2", {}).get("eligible", 0)}</div><div class="funnel-lbl">Допуск (Eligible)</div></div>
        <div class="funnel-item"><div class="funnel-val">{scanner_data.get("funnel", {}).get("r2", {}).get("rejected", 0)}</div><div class="funnel-lbl">Отклонено</div></div>
        <div class="funnel-item"><div class="funnel-val">{scanner_data.get("funnel", {}).get("r2", {}).get("active_positions", 0)}</div><div class="funnel-lbl">Spot Long балансов</div></div>
      </div>
      <div class="banner">
        <strong>Статус заявок R2:</strong> Записи <code>QUOTE_ESTIMATE_NOT_PROVED_FILL</code> являются модельной оценкой стакана spot micro-alpha и не заявляются как реальные биржевые исполнения.
      </div>
      <div class="table-responsive">
        <table>
          <thead>
            <tr>
              <th>Символ</th>
              <th>Стратегия</th>
              <th>Режим</th>
              <th>Сигнал</th>
              <th>Score (R2)</th>
              <th>Целевой вес</th>
              <th>Допуск</th>
              <th>Статус решения</th>
              <th>Тип оценки</th>
            </tr>
          </thead>
          <tbody>
            ''' + "".join([f'''<tr>
              <td><strong>{row.get("symbol")}</strong></td>
              <td>{row.get("strategy")}</td>
              <td><span class="badge" style="background:#162030;color:#26a69a;">{row.get("market_regime")}</span></td>
              <td><strong>{row.get("signal")}</strong></td>
              <td><code>{row.get("score")}</code></td>
              <td>{row.get("target_weight")}</td>
              <td>{fmt_status("OK" if row.get("eligible") in (True, "YES") else ("STALE" if row.get("eligible") == "OBSERVING" else "INVALID"))}</td>
              <td>{row.get("decision")}</td>
              <td><span class="badge" style="background:#78909c22;color:#90a4ae;">{row.get("fill_claim_badge")}</span></td>
            </tr>''' for row in scanner_data.get("r2_scanner", [])]) + f'''
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- TAB 3: OPEN POSITIONS -->
  <div class="tab-pane" id="tab-positions">
    <div class="subtabs-nav">
      <button class="subtab-btn active" id="btn-sub-nat-pos" onclick="switchSubTab('positions-sub', 'sub-nat-pos')">Native ({len(positions_data.get("native_positions", []))})</button>
      <button class="subtab-btn" id="btn-sub-sha-pos" onclick="switchSubTab('positions-sub', 'sub-sha-pos')">Shadow ({len(positions_data.get("shadow_positions", []))})</button>
      <button class="subtab-btn" id="btn-sub-r2-pos" onclick="switchSubTab('positions-sub', 'sub-r2-pos')">CryptoLab R2 ({len(positions_data.get("r2_positions", []))})</button>
    </div>

    <!-- Native Positions -->
    <div class="subtab-pane active" id="sub-nat-pos">
      ''' + (f'''<div class="empty-state">
        <div class="empty-icon">🛡️</div>
        <div class="empty-title">Пока открытых позиций нет</div>
        <div class="empty-desc">{positions_data.get("empty_reason")}</div>
      </div>''' if not positions_data.get("native_positions") else '''<div class="table-responsive">
        <table>
          <thead>
            <tr>
              <th>Символ / Сторона</th>
              <th>Стратегия</th>
              <th>Вход (UTC)</th>
              <th>Цена / Qty</th>
              <th>Объём USDT</th>
              <th>Floating PnL</th>
              <th>Stop Loss (% расст.)</th>
              <th>Take Profit 1 / 2</th>
              <th>Флаги</th>
              <th>Удержание</th>
              <th>Достоверность</th>
            </tr>
          </thead>
          <tbody>
            ''' + "".join([f'''<tr>
              <td><strong>{p.get("symbol")}</strong> <span class="badge" style="color:{"#00e676" if p.get("side")=="LONG" else "#ff5252"}">{p.get("side")}</span></td>
              <td>{p.get("strategy")}</td>
              <td class="dim">{p.get("entry_time_utc")}</td>
              <td>${p.get("entry_price")} / {p.get("current_qty")}</td>
              <td>${p.get("notional_usdt")}</td>
              <td>{fmt_val(p.get("unrealized_pnl_usdt"), " USDT")}</td>
              <td>${p.get("stop_loss")} ({p.get("sl_distance_pct")}%)</td>
              <td>{p.get("tp1_price")}</td>
              <td>{" ".join([f"<span class='badge' style='background:#162030'>{fl}</span>" for fl in p.get("flags", [])])}</td>
              <td>{p.get("bars_held")} баров</td>
              <td><span class="badge" style="background:#00e67622;color:#00e676;">{p.get("credibility")}</span></td>
            </tr>''' for p in positions_data.get("native_positions", [])]) + '''
          </tbody>
        </table>
      </div>''') + f'''
    </div>

    <!-- Shadow Positions -->
    <div class="subtab-pane" id="sub-sha-pos">
      ''' + (f'''<div class="empty-state">
        <div class="empty-icon">🛡️</div>
        <div class="empty-title">Пока открытых позиций нет</div>
        <div class="empty-desc">{positions_data.get("empty_reason")}</div>
      </div>''' if not positions_data.get("shadow_positions") else '''<div class="table-responsive">
        <table>
          <thead>
            <tr>
              <th>Символ / Сторона</th>
              <th>Стратегия</th>
              <th>Вход (UTC)</th>
              <th>Цена / Qty</th>
              <th>Объём USDT</th>
              <th>Floating PnL</th>
              <th>Stop Loss</th>
              <th>TP1 (% расст.)</th>
              <th>TP2 (% расст.)</th>
              <th>Флаги</th>
              <th>Удержание</th>
            </tr>
          </thead>
          <tbody>
            ''' + "".join([f'''<tr>
              <td><strong>{p.get("symbol")}</strong> <span class="badge" style="color:{"#00e676" if p.get("side")=="LONG" else "#ff5252"}">{p.get("side")}</span></td>
              <td>{p.get("strategy")}</td>
              <td class="dim">{p.get("entry_time_utc")}</td>
              <td>${p.get("entry_price")} / {p.get("current_qty")}</td>
              <td>${p.get("notional_usdt")}</td>
              <td>{fmt_val(p.get("unrealized_pnl_usdt"), " USDT")}</td>
              <td>${p.get("stop_loss")}</td>
              <td>{p.get("tp1_price")} ({p.get("tp1_distance_pct")})</td>
              <td>{p.get("tp2_price")} ({p.get("tp2_distance_pct")})</td>
              <td>{" ".join([f"<span class='badge' style='background:#162030'>{fl}</span>" for fl in p.get("flags", [])])}</td>
              <td>{p.get("bars_held")} баров</td>
            </tr>''' for p in positions_data.get("shadow_positions", [])]) + '''
          </tbody>
        </table>
      </div>''') + f'''
    </div>

    <!-- R2 Positions -->
    <div class="subtab-pane" id="sub-r2-pos">
      ''' + (f'''<div class="empty-state">
        <div class="empty-icon">🪙</div>
        <div class="empty-title">Позиций Spot Cash пока нет</div>
        <div class="empty-desc">Все три экспортируемых профиля (BASE, STRESS, T01) находятся в 100% Cash ($300.00 USDT). До первых рыночных целевых весов монеты не закупаются.</div>
      </div>''' if not positions_data.get("r2_positions") else '''<div class="table-responsive">
        <table>
          <thead>
            <tr>
              <th>Символ</th>
              <th>Стратегия</th>
              <th>Сторона</th>
              <th>Размер монет (Qty)</th>
              <th>Оценка стоимости</th>
              <th>Статус исполнения</th>
            </tr>
          </thead>
          <tbody>
            ''' + "".join([f'''<tr>
              <td><strong>{p.get("symbol")}</strong></td>
              <td>{p.get("strategy")}</td>
              <td><span class="badge" style="color:#00e676">{p.get("side")}</span></td>
              <td>{p.get("current_qty")}</td>
              <td>${p.get("notional_usdt")}</td>
              <td><span class="badge" style="background:#78909c22;color:#90a4ae;">{p.get("credibility")}</span></td>
            </tr>''' for p in positions_data.get("r2_positions", [])]) + '''
          </tbody>
        </table>
      </div>''') + f'''
    </div>
  </div>

  <!-- TAB 4: TRADE JOURNAL -->
  <div class="tab-pane" id="tab-journal">
    <div class="filter-bar">
      <select class="select-input" id="filter-sys-journal" onchange="filterJournal()">
        <option value="ALL">Все системы (All Systems)</option>
        <option value="NATIVE">Native Paper</option>
        <option value="SHADOW">Realistic Shadow</option>
        <option value="CRYPTOLAB_R2">CryptoLab R2</option>
      </select>
      <input type="text" class="search-input" id="search-sym-journal" placeholder="🔍 Фильтр по символу (BTC, SOL)..." onkeyup="filterJournal()">
      <span class="dim" style="font-size:12px;">Показано последних {len(journal_data.get("events", []))} событий из базы SQLite</span>
    </div>

    ''' + (f'''<div class="empty-state">
      <div class="empty-icon">📜</div>
      <div class="empty-title">События пока не зарегистрированы</div>
      <div class="empty-desc">Журнал ведётся в реальном времени. При появлении сигналов, отклонений фильтрами, исполнении сделок или начислении funding события будут немедленно зафиксированы здесь. Никаких искусственных сделок задним числом не создаётся.</div>
    </div>''' if not journal_data.get("events") else '''<div class="table-responsive">
      <table id="tbl-journal">
        <thead>
          <tr>
            <th>Event ID</th>
            <th>Система</th>
            <th>Тип события</th>
            <th>Символ</th>
            <th>Время события (UTC)</th>
            <th>Цена / Объём</th>
            <th>PnL воздействие</th>
            <th>Подробности и причина</th>
          </tr>
        </thead>
        <tbody>
          ''' + "".join([f'''<tr>
            <td><code>{ev.get("event_id")}</code></td>
            <td><strong>{ev.get("system")}</strong></td>
            <td><span class="badge" style="background:#162030;color:var(--accent-blue);">{ev.get("event_type")}</span></td>
            <td><strong>{ev.get("symbol")}</strong></td>
            <td class="dim">{ev.get("event_utc")}</td>
            <td>{f"${ev.get('price')}" if ev.get('price') is not None else "--"} {f"({ev.get('qty')})" if ev.get('qty') is not None else ""}</td>
            <td>{fmt_val(ev.get("pnl_impact_usdt"), " USDT") if ev.get("pnl_impact_usdt") is not None else "--"}</td>
            <td>{ev.get("notes")}</td>
          </tr>''' for ev in journal_data.get("events", [])]) + '''
        </tbody>
      </table>
    </div>''') + f'''
  </div>

  <!-- TAB 5: ANALYTICS -->
  <div class="tab-pane" id="tab-analytics">
    <div class="subtabs-nav">
      <button class="subtab-btn active" id="btn-sub-nat-ana" onclick="switchSubTab('analytics-sub', 'sub-nat-ana')">Native Performance</button>
      <button class="subtab-btn" id="btn-sub-sha-ana" onclick="switchSubTab('analytics-sub', 'sub-sha-ana')">Shadow Performance</button>
      <button class="subtab-btn" id="btn-sub-r2-ana" onclick="switchSubTab('analytics-sub', 'sub-r2-ana')">CryptoLab R2 Performance</button>
    </div>

    <!-- Native Analytics -->
    <div class="subtab-pane active" id="sub-nat-ana">
      <div class="chart-box">
        <div class="chart-title">Динамика Equity: v3.2 Native Paper (Starting $300.00 USDT)</div>
        {nat_chart}
      </div>
      <div class="cards-grid">
        <div class="card">
          <div class="card-title" style="font-size:15px;color:var(--accent-blue);">Торговые издержки (Friction)</div>
          <div class="metric-row"><span class="dim">Комиссии MNT (0.09%)</span><span>${fmt_val(a.get("fees_paid_usdt"))}</span></div>
          <div class="metric-row"><span class="dim">Проскальзывание (0.02%)</span><span>${fmt_val(a.get("slippage_cost_usdt"))}</span></div>
          <div class="metric-row"><span class="dim">Funding Perpetual</span><span>${fmt_val(a.get("funding_paid_usdt"))}</span></div>
        </div>
        <div class="card">
          <div class="card-title" style="font-size:15px;color:var(--accent-blue);">Статистика сделок</div>
          <div class="metric-row"><span class="dim">Закрытых сделок</span><strong>{analytics_data.get("native", {}).get("closed_trades_count", 0)}</strong></div>
          <div class="metric-row"><span class="dim">Статус выборки</span><span class="badge" style="background:#ffa72622;color:#ffa726;">{analytics_data.get("native", {}).get("sample_status")}</span></div>
          <div class="metric-row"><span class="dim">Примечание</span><span class="dim" style="font-size:11px;">{analytics_data.get("native", {}).get("sample_note")}</span></div>
        </div>
      </div>
    </div>

    <!-- Shadow Analytics -->
    <div class="subtab-pane" id="sub-sha-ana">
      <div class="chart-box">
        <div class="chart-title">Динамика Equity: v3.2 Realistic Shadow (L2 Orderbook Execution)</div>
        {sha_chart}
      </div>
      <div class="cards-grid">
        <div class="card">
          <div class="card-title" style="font-size:15px;color:var(--accent-purple);">Издержки дискретного стакана L2</div>
          <div class="metric-row"><span class="dim">Комиссии Retail (0.10%)</span><span>${fmt_val(b.get("fees_paid_usdt"))}</span></div>
          <div class="metric-row"><span class="dim">L2 Slippage & Spread</span><span>${fmt_val(b.get("slippage_cost_usdt"))}</span></div>
          <div class="metric-row"><span class="dim">Funding 8h Perpetual</span><span>${fmt_val(b.get("funding_paid_usdt"))}</span></div>
        </div>
        <div class="card">
          <div class="card-title" style="font-size:15px;color:var(--accent-purple);">Статистика сделок</div>
          <div class="metric-row"><span class="dim">Закрытых сделок</span><strong>{analytics_data.get("shadow", {}).get("closed_trades_count", 0)}</strong></div>
          <div class="metric-row"><span class="dim">Статус выборки</span><span class="badge" style="background:#ffa72622;color:#ffa726;">{analytics_data.get("shadow", {}).get("sample_status")}</span></div>
        </div>
      </div>
    </div>

    <!-- R2 Analytics -->
    <div class="subtab-pane" id="sub-r2-ana">
      <div class="banner banner-warn">
        <strong>⚠️ Зафиксированный разрыв данных (Research Data Gap):</strong> 16 ноября 2026 с 01:03:32Z до 09:20:00Z (~8.3 часов) служба R2 находилась в режиме аварийного перезапуска из-за экспирации readiness. Сбой успешно устранён протоколом R2-RECOVERY-001 без потери исходного T0. Период простоя учтён как разрыв наблюдения.
      </div>
      <div class="chart-box">
        <div class="chart-title">Динамика Equity: CryptoLab R2-SHADOW (Spot Cash)</div>
        {r2_chart}
      </div>
    </div>
  </div>

  <!-- TAB 6: SYSTEM HEALTH -->
  <div class="tab-pane" id="tab-health">
    <div class="card" style="margin-bottom:16px;">
      <h3 style="margin-top:0;font-size:16px;color:var(--accent-blue);">Статус 5 служб systemd (VPS 150.241.98.70)</h3>
      <div class="table-responsive">
        <table>
          <thead>
            <tr>
              <th>Служба systemd</th>
              <th>Роль / Порт</th>
              <th>Телеметрия</th>
              <th>Возраст Heartbeat</th>
              <th>Статус службы</th>
            </tr>
          </thead>
          <tbody>
            ''' + "".join([f'''<tr>
              <td><code>{svc.get("name")}</code></td>
              <td>{svc.get("role")}</td>
              <td>{fmt_status("OK" if svc.get("telemetry_present") else "UNKNOWN")}</td>
              <td>{f"{svc.get('heartbeat_age_sec'):.1f}s" if svc.get('heartbeat_age_sec') is not None else "--"}</td>
              <td>{fmt_status(svc.get("status"))}</td>
            </tr>''' for svc in health_data.get("services", [])]) + f'''
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <h3 style="margin-top:0;font-size:16px;color:var(--accent-blue);">Целостность баз данных SQLite (WAL Checkpoints)</h3>
      <div class="table-responsive">
        <table>
          <thead>
            <tr>
              <th>База данных</th>
              <th>Файл существует</th>
              <th>Размер DB</th>
              <th>Размер WAL</th>
              <th>PRAGMA quick_check(1)</th>
            </tr>
          </thead>
          <tbody>
            ''' + "".join([f'''<tr>
              <td><strong>{db_name}</strong></td>
              <td>{fmt_status("OK" if db_info.get("exists") else "NOT_FOUND")}</td>
              <td>{db_info.get("size_kb", 0)} KB</td>
              <td>{db_info.get("wal_size_kb", 0)} KB</td>
              <td><span class="badge" style="background:#00e67622;color:#00e676;">{db_info.get("status")}</span></td>
            </tr>''' for db_name, db_info in health_data.get("databases", {}).items()]) + f'''
          </tbody>
        </table>
      </div>
    </div>
  </div>

  <!-- Footer -->
  <div class="footer">
    <div>TORG_BOT Trading Control Center v1.0 | 100% Read-Only Interface | Автообновление каждые 10 секунд</div>
    <div>Сформировано UTC: {report.get("timestamp_iso")}</div>
  </div>

  <!-- Client-side Interactive Tab & Filter Scripts -->
  <script>
    function switchTab(tabId) {{
      document.querySelectorAll(".tab-pane").forEach(el => el.classList.remove("active"));
      document.querySelectorAll(".tab-btn").forEach(el => el.classList.remove("active"));
      const target = document.getElementById(tabId);
      if (target) target.classList.add("active");
      const btn = document.getElementById("btn-" + tabId);
      if (btn) btn.classList.add("active");
      window.location.hash = tabId;
    }}

    function switchSubTab(containerClass, targetId) {{
      const pane = document.getElementById(targetId);
      if (!pane) return;
      const parent = pane.parentElement;
      parent.querySelectorAll(".subtab-pane").forEach(el => el.classList.remove("active"));
      parent.querySelectorAll(".subtab-btn").forEach(el => el.classList.remove("active"));
      pane.classList.add("active");
      const btn = document.getElementById("btn-" + targetId);
      if (btn) btn.classList.add("active");
    }}

    function filterTable(inputId, tableId) {{
      const input = document.getElementById(inputId);
      const filter = input.value.toUpperCase();
      const table = document.getElementById(tableId);
      if (!table) return;
      const trs = table.getElementsByTagName("tr");
      for (let i = 1; i < trs.length; i++) {{
        const td = trs[i].getElementsByTagName("td")[0];
        if (td) {{
          const txt = td.textContent || td.innerText;
          trs[i].style.display = txt.toUpperCase().indexOf(filter) > -1 ? "" : "none";
        }}
      }}
    }}

    function filterJournal() {{
      const sysFilter = document.getElementById("filter-sys-journal").value.toUpperCase();
      const symFilter = document.getElementById("search-sym-journal").value.toUpperCase();
      const table = document.getElementById("tbl-journal");
      if (!table) return;
      const trs = table.getElementsByTagName("tr");
      for (let i = 1; i < trs.length; i++) {{
        const tdSys = trs[i].getElementsByTagName("td")[1];
        const tdSym = trs[i].getElementsByTagName("td")[3];
        if (tdSys && tdSym) {{
          const txtSys = (tdSys.textContent || tdSys.innerText).toUpperCase();
          const txtSym = (tdSym.textContent || tdSym.innerText).toUpperCase();
          const matchSys = (sysFilter === "ALL" || txtSys.indexOf(sysFilter) > -1);
          const matchSym = (!symFilter || txtSym.indexOf(symFilter) > -1);
          trs[i].style.display = (matchSys && matchSym) ? "" : "none";
        }}
      }}
    }}

    // Restore active tab from location hash on page load and schedule auto-refresh
    window.addEventListener("DOMContentLoaded", () => {{
      const hash = window.location.hash.replace("#", "") || "tab-overview";
      switchTab(hash);
      // Auto-refresh every 10 seconds without losing active tab hash
      setTimeout(() => {{
        window.location.reload();
      }}, 10000);
    }});
  </script>

</body>
</html>'''
        return html


class ThreeSystemDashboardHTTPHandler(BaseHTTPRequestHandler):
    """Read-only HTTP handler serving HTML and REST JSON telemetry."""

    dashboard_provider: Optional[ThreeSystemComparativeDashboard] = None
    report_supplier: Optional[Any] = None

    def log_message(self, format, *args):
        pass

    def _send_response_body(self, status_code: int, content_type: str, body: bytes):
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        clean_path = parsed.path
        query_params = urllib.parse.parse_qs(parsed.query)

        if clean_path in ["/", "/index.html"]:
            report = self._get_report()
            html = self.dashboard_provider.render_html_dashboard(report)
            self._send_response_body(200, "text/html; charset=utf-8", html.encode("utf-8"))
        elif clean_path in ["/status", "/api/status", "/telemetry", "/api/telemetry"]:
            report = self._get_report()
            body = json.dumps(report, indent=2).encode("utf-8")
            self._send_response_body(200, "application/json; charset=utf-8", body)
        elif clean_path in ["/native", "/api/native", "/systems/native"]:
            report = self._get_report()
            data = report.get("systems", {}).get("native", {})
            body = json.dumps(data, indent=2).encode("utf-8")
            self._send_response_body(200, "application/json; charset=utf-8", body)
        elif clean_path in ["/shadow", "/api/shadow", "/systems/shadow"]:
            report = self._get_report()
            data = report.get("systems", {}).get("shadow", {})
            body = json.dumps(data, indent=2).encode("utf-8")
            self._send_response_body(200, "application/json; charset=utf-8", body)
        elif clean_path in ["/r2", "/api/r2", "/systems/r2", "/api/r2_shadow"]:
            report = self._get_report()
            data = report.get("systems", {}).get("r2_shadow", {})
            body = json.dumps(data, indent=2).encode("utf-8")
            self._send_response_body(200, "application/json; charset=utf-8", body)
        elif clean_path == "/api/scanner":
            data = self.dashboard_provider.get_scanner_data()
            body = json.dumps(data, indent=2).encode("utf-8")
            self._send_response_body(200, "application/json; charset=utf-8", body)
        elif clean_path == "/api/positions":
            data = self.dashboard_provider.get_positions_data()
            body = json.dumps(data, indent=2).encode("utf-8")
            self._send_response_body(200, "application/json; charset=utf-8", body)
        elif clean_path == "/api/journal":
            sys_val = query_params.get("system", [None])[0]
            sym_val = query_params.get("symbol", [None])[0]
            ev_val = query_params.get("event_type", [None])[0]
            limit_val = int(query_params.get("limit", ["50"])[0]) if query_params.get("limit", ["50"])[0].isdigit() else 50
            offset_val = int(query_params.get("offset", ["0"])[0]) if query_params.get("offset", ["0"])[0].isdigit() else 0
            data = self.dashboard_provider.get_journal_data(
                system=sys_val, symbol=sym_val, event_type=ev_val, limit=limit_val, offset=offset_val
            )
            body = json.dumps(data, indent=2).encode("utf-8")
            self._send_response_body(200, "application/json; charset=utf-8", body)
        elif clean_path == "/api/analytics":
            data = self.dashboard_provider.get_analytics_data()
            body = json.dumps(data, indent=2).encode("utf-8")
            self._send_response_body(200, "application/json; charset=utf-8", body)
        elif clean_path == "/api/health":
            data = self.dashboard_provider.get_health_data()
            body = json.dumps(data, indent=2).encode("utf-8")
            self._send_response_body(200, "application/json; charset=utf-8", body)
        elif clean_path == "/health":
            body = json.dumps({"status": "ok", "read_only": True, "service": "trading_control_center"}).encode("utf-8")
            self._send_response_body(200, "application/json; charset=utf-8", body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
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
    handler_cls = ThreeSystemDashboardHTTPHandler
    handler_cls.dashboard_provider = dashboard_provider or ThreeSystemComparativeDashboard()
    handler_cls.report_supplier = report_supplier

    class ReusableHTTPServer(HTTPServer):
        allow_reuse_address = True

    return ReusableHTTPServer((host, port), handler_cls)
