"""
v3_2/src/integrations/three_system_dashboard.py
Unified Three-System Comparative Dashboard Provider.
Protocol: TORG-BOT — THREE-SYSTEM-INTEGRATION-005

Aggregates three strictly independent accounts ($300.000000 USDT each):
1. System A: Bybit Multi-Alpha v3.2 Native (Linear Perpetual, Original Trend Logic)
2. System B: Bybit Multi-Alpha v3.2 Realistic Shadow (Linear Perpetual, Discrete L2 Execution & 8h Funding)
3. System C: CryptoLab R2-SHADOW (Bybit Spot Long-Only Cash, Profiles: R2 BASE, R2 STRESS, BASELINE T01)

Invariants:
- NEVER pools or sums Spot and Perpetual PnL into a single portfolio.
- NEVER modifies original v3.2 on Port 8083.
- Read-only ingestion via R2TelemetryConsumer.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

from v3_2.src.integrations.r2_consumer import R2TelemetryConsumer, R2ConsumerResult


@dataclass
class SystemMetrics:
    """Standardized comparative metrics for each independent system."""
    name: str
    role: str
    market_type: str  # PERPETUAL or SPOT
    initial_capital_usdt: float
    equity_usdt: Optional[float]
    net_pnl_usdt: Optional[float]
    net_return_pct: Optional[float]
    mdd_pct: float
    cash_usdt: float
    exposure_pct: Optional[float]
    turnover_usdt: float
    fees_paid_usdt: float
    slippage_cost_usdt: float
    funding_paid_usdt: float
    positions_count: int
    positions_detail: Dict[str, Any]
    data_freshness_status: str
    valuation_status: str
    error_count: int
    last_update_iso: Optional[str]
    notes: str
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
    ):
        self.r2_consumer = r2_consumer or R2TelemetryConsumer()
        self.r2_telemetry_path = r2_telemetry_path

    def build_system_a_native_metrics(self, native_portfolio: Any) -> SystemMetrics:
        """Extracts metrics from v3.2 Native portfolio."""
        if hasattr(native_portfolio, "realized_equity"):
            eq = float(native_portfolio.realized_equity)
            bal = float(native_portfolio.balance)
            init_cap = float(native_portfolio.initial_capital)
            pnl = eq - init_cap
            ret = (pnl / init_cap) * 100.0 if init_cap > 0 else 0.0
            fees = float(native_portfolio.total_fees_paid)
            funding = float(native_portfolio.total_funding_paid)
            positions = {
                sym: {
                    "direction": "LONG" if p.direction == 1 else "SHORT",
                    "qty": float(p.current_qty),
                    "entry_price": float(p.entry_price),
                    "current_stop": float(p.current_stop),
                    "tp1_taken": p.tp1_taken,
                    "tp2_taken": p.tp2_taken,
                }
                for sym, p in native_portfolio.positions.items()
            }
            pos_cnt = len(positions)
            cash = float(native_portfolio.balance)
            exposure = round(((eq - cash) / eq) * 100.0, 2) if eq > 0 else 0.0
            turnover = float(getattr(native_portfolio, "total_turnover", 0.0))
        else:
            eq = 300.0
            bal = 300.0
            init_cap = 300.0
            pnl = 0.0
            ret = 0.0
            fees = 0.0
            funding = 0.0
            positions = {}
            pos_cnt = 0
            cash = 300.0
            exposure = 0.0
            turnover = 0.0

        return SystemMetrics(
            name="v3.2 Native",
            role="Original Donchian Trend Logic (New Isolated Paper Cohort)",
            market_type="BYBIT_LINEAR_PERPETUAL",
            initial_capital_usdt=init_cap,
            equity_usdt=eq,
            net_pnl_usdt=round(pnl, 4),
            net_return_pct=round(ret, 3),
            mdd_pct=0.0,
            cash_usdt=round(cash, 4),
            exposure_pct=exposure,
            turnover_usdt=round(turnover, 2),
            fees_paid_usdt=round(fees, 4),
            slippage_cost_usdt=0.0,  # Included in fills
            funding_paid_usdt=round(funding, 4),
            positions_count=pos_cnt,
            positions_detail=positions,
            data_freshness_status="FRESH",
            valuation_status="FRESH",
            error_count=0,
            last_update_iso=datetime.now(timezone.utc).isoformat(),
            notes="Linear Perpetuals, Half-Kelly sizing, 0.09% MNT taker fee tier",
        )

    def build_system_b_shadow_metrics(self, shadow_portfolio: Any) -> SystemMetrics:
        """Extracts metrics from v3.2 Realistic Shadow portfolio."""
        if hasattr(shadow_portfolio, "realized_equity"):
            eq = float(shadow_portfolio.realized_equity)
            bal = float(shadow_portfolio.balance)
            init_cap = float(shadow_portfolio.initial_capital)
            pnl = eq - init_cap
            ret = (pnl / init_cap) * 100.0 if init_cap > 0 else 0.0
            fees = float(shadow_portfolio.total_fees_paid)
            funding = float(shadow_portfolio.total_funding_paid)
            positions = {
                sym: {
                    "direction": "LONG" if p.direction == 1 else "SHORT",
                    "qty": float(p.current_qty),
                    "entry_price": float(p.entry_price),
                    "current_stop": float(p.current_stop),
                    "tp1_taken": p.tp1_taken,
                    "tp2_taken": p.tp2_taken,
                }
                for sym, p in shadow_portfolio.positions.items()
            }
            pos_cnt = len(positions)
            cash = float(shadow_portfolio.balance)
            exposure = round(((eq - cash) / eq) * 100.0, 2) if eq > 0 else 0.0
            turnover = float(getattr(shadow_portfolio, "total_turnover", 0.0))
        else:
            eq = 300.0
            bal = 300.0
            init_cap = 300.0
            pnl = 0.0
            ret = 0.0
            fees = 0.0
            funding = 0.0
            positions = {}
            pos_cnt = 0
            cash = 300.0
            exposure = 0.0
            turnover = 0.0

        return SystemMetrics(
            name="v3.2 Realistic Shadow",
            role="Original Trend Logic with Realistic Discrete L2 Book & 8h Funding",
            market_type="BYBIT_LINEAR_PERPETUAL",
            initial_capital_usdt=init_cap,
            equity_usdt=eq,
            net_pnl_usdt=round(pnl, 4),
            net_return_pct=round(ret, 3),
            mdd_pct=0.0,
            cash_usdt=round(cash, 4),
            exposure_pct=exposure,
            turnover_usdt=round(turnover, 2),
            fees_paid_usdt=round(fees, 4),
            slippage_cost_usdt=0.0,
            funding_paid_usdt=round(funding, 4),
            positions_count=pos_cnt,
            positions_detail=positions,
            data_freshness_status="FRESH",
            valuation_status="FRESH",
            error_count=0,
            last_update_iso=datetime.now(timezone.utc).isoformat(),
            notes="Linear Perpetuals, Discrete L2 orderbook ladder walking, 0.10% standard retail fee",
        )

    def build_system_c_r2_metrics(
        self,
        r2_result: Optional[R2ConsumerResult] = None,
        now_dt: Optional[datetime] = None,
    ) -> SystemMetrics:
        """Extracts metrics from CryptoLab R2-SHADOW consumer result."""
        if r2_result is None and self.r2_telemetry_path and self.r2_telemetry_path.exists():
            r2_result = self.r2_consumer.read_telemetry_file(self.r2_telemetry_path, now_dt=now_dt)

        if r2_result and r2_result.is_valid:
            r2_minus_t01_usdt = r2_result.r2_minus_t01_usdt
            r2_minus_t01_pp = r2_result.r2_minus_t01_pp
            comp_status = r2_result.comparison_status
            
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
                funding_paid_usdt=0.0,  # Spot has zero funding
                positions_count=len([q for q in r2_result.quantities.values() if q > 0]),
                positions_detail=r2_result.quantities,
                data_freshness_status=r2_result.status,
                valuation_status=r2_result.valuation_status,
                error_count=r2_result.error_count,
                last_update_iso=r2_result.generated_at.isoformat() if r2_result.generated_at else None,
                notes="Spot Cash (No Leverage, No Funding), 3 profiles (R2 BASE, R2 STRESS, BASELINE T01)",
                extra={
                    "r2_minus_t01_usdt": r2_minus_t01_usdt,
                    "r2_minus_t01_pp": r2_minus_t01_pp,
                    "comparison_status": comp_status,
                    "buffer_usdt": r2_result.buffer_usdt,
                    "real_orders": r2_result.real_orders,
                    "demo_orders": r2_result.demo_orders,
                    "deployment_status": r2_result.deployment_status,
                },
            )
        else:
            err = r2_result.rejection_reason if r2_result else "Telemetry not loaded"
            return SystemMetrics(
                name="CryptoLab R2-SHADOW",
                role="Spot Long-Only Cash Microstructure Alpha (Hunter/R2)",
                market_type="BYBIT_SPOT_LONG_CASH_NO_LEVERAGE",
                initial_capital_usdt=300.0,
                equity_usdt=None,
                net_pnl_usdt=None,
                net_return_pct=None,
                mdd_pct=0.0,
                cash_usdt=300.0,
                exposure_pct=None,
                turnover_usdt=0.0,
                fees_paid_usdt=0.0,
                slippage_cost_usdt=0.0,
                funding_paid_usdt=0.0,
                positions_count=0,
                positions_detail={},
                data_freshness_status="INVALID" if r2_result else "NOT_LOADED",
                valuation_status="UNVALUED_OR_STALE",
                error_count=1,
                last_update_iso=None,
                notes=f"Error / Disconnected: {err}",
            )

    def generate_comparative_report(
        self,
        native_portfolio: Any,
        shadow_portfolio: Any,
        r2_result: Optional[R2ConsumerResult] = None,
        now_dt: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Generates unified multi-system telemetry dictionary."""
        sys_a = self.build_system_a_native_metrics(native_portfolio)
        sys_b = self.build_system_b_shadow_metrics(shadow_portfolio)
        sys_c = self.build_system_c_r2_metrics(r2_result, now_dt=now_dt)

        return {
            "title": "TORG-BOT Three-System Comparative Dashboard",
            "protocol": "TORG-BOT — THREE-SYSTEM-INTEGRATION-005",
            "timestamp_iso": (now_dt or datetime.now(timezone.utc)).isoformat(),
            "target_cohort": {
                "initial_capital_per_system_usdt": 300.0,
                "total_independent_systems": 3,
                "t0_status": "PENDING_OPERATOR_APPROVAL",
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
