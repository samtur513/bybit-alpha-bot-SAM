"""
Streak-Based Anti-Martingale Dynamic Risk & R:R Manager for v3.3 Turbo.
Manages adaptive risk gearing based on winning / losing streaks and drawdown:
- Gear 1 (Base / Protective): Risk 1.5%, Target R:R 1:3.0 - 1:4.0, Pyramiding OFF
- Gear 2 (Momentum Accelerator): Risk 2.2%, Target R:R 1:3.5 - 1:4.5, Pyramiding +50%
- Gear 3 (Hot Streak / Turbo Max): Risk 3.2%, Target R:R 1:4.0 - 1:5.5+, Pyramiding +75%

Stepdown Logic:
- Solid Win (> 0.3 * 1R): streak_wins += 1, streak_losses = 0, step up gear (+1)
- Solid Loss (< -0.3 * 1R): streak_losses += 1, streak_wins = 0, step down gear (-1)
- Scratch / Breakeven (|PnL| <= 0.3 * 1R): neutral, preserves current gear & streak
- Drawdown Circuit Breaker: If DD >= 10.0%, lock into Gear 1 until DD recovers < 5.0%.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("StreakRiskManager")


@dataclass
class GearConfig:
    gear: int
    name: str
    risk_pct: Decimal
    tp1_r_mult: Decimal
    tp2_r_mult: Decimal
    tp1_ratio: Decimal
    tp2_ratio: Decimal
    pyramiding_allowed: bool
    pyramid_ratio: Decimal
    rr_label: str


GEAR_CONFIGS: Dict[int, GearConfig] = {
    1: GearConfig(
        gear=1,
        name="PROTECTIVE (1:3)",
        risk_pct=Decimal("0.015"),       # 1.5% base risk
        tp1_r_mult=Decimal("3.0"),       # TP1 at +3.0R
        tp2_r_mult=Decimal("4.0"),       # TP2 at +4.0R
        tp1_ratio=Decimal("0.35"),       # 35% size at TP1 -> Stop to BE
        tp2_ratio=Decimal("0.35"),       # 35% size at TP2 -> Stop to +1.5R
        pyramiding_allowed=False,        # Pyramiding OFF
        pyramid_ratio=Decimal("0.0"),
        rr_label="1:3.0 — 1:4.0",
    ),
    2: GearConfig(
        gear=2,
        name="MOMENTUM (1:4)",
        risk_pct=Decimal("0.022"),       # 2.2% moderate risk
        tp1_r_mult=Decimal("3.5"),       # TP1 at +3.5R
        tp2_r_mult=Decimal("4.5"),       # TP2 at +4.5R
        tp1_ratio=Decimal("0.35"),       # 35% size at TP1 -> Stop to BE
        tp2_ratio=Decimal("0.35"),       # 35% size at TP2 -> Stop to +2.0R
        pyramiding_allowed=True,         # Pyramiding +50%
        pyramid_ratio=Decimal("0.50"),
        rr_label="1:3.5 — 1:4.5",
    ),
    3: GearConfig(
        gear=3,
        name="TURBO_MAX (1:5)",
        risk_pct=Decimal("0.032"),       # 3.2% maximum aggressive risk
        tp1_r_mult=Decimal("4.0"),       # TP1 at +4.0R
        tp2_r_mult=Decimal("5.5"),       # TP2 at +5.5R
        tp1_ratio=Decimal("0.30"),       # 30% size at TP1 -> Stop to BE
        tp2_ratio=Decimal("0.35"),       # 35% size at TP2 -> Stop to +2.5R
        pyramiding_allowed=True,         # Pyramiding +75%
        pyramid_ratio=Decimal("0.75"),
        rr_label="1:4.0 — 1:5.5+",
    ),
}


@dataclass
class StreakSizingResult:
    allowed: bool
    qty: Decimal
    risk_usd: Decimal
    effective_risk_pct: Decimal
    gear: int
    reason: str = ""


class StreakRiskManager:
    """
    Stateful risk manager providing streak-based dynamic sizing and R:R gear transitions.
    """

    def __init__(
        self,
        initial_capital: Decimal = Decimal("300.0"),
        initial_gear: int = 1,
        max_leverage_cap: Decimal = Decimal("4.0"),
        circuit_breaker_dd_pct: Decimal = Decimal("10.0"),
        recovery_dd_pct: Decimal = Decimal("5.0"),
        scratch_threshold_ratio: Decimal = Decimal("0.30"),
    ):
        self.initial_capital = initial_capital
        self.current_gear = initial_gear
        self.streak_wins = 0
        self.streak_losses = 0
        self.total_trades_processed = 0
        self.max_leverage_cap = max_leverage_cap
        self.circuit_breaker_dd_pct = circuit_breaker_dd_pct
        self.recovery_dd_pct = recovery_dd_pct
        self.scratch_threshold_ratio = scratch_threshold_ratio
        self.is_circuit_breaker_active = False

    def get_config(self) -> GearConfig:
        """Returns the GearConfig for the active gear."""
        return GEAR_CONFIGS.get(self.current_gear, GEAR_CONFIGS[1])

    def get_effective_gear(self, current_dd: Decimal = Decimal("0")) -> int:
        """
        Returns active gear considering drawdown circuit breaker:
        If current_dd >= 10.0%, locks into Gear 1.
        """
        if current_dd >= self.circuit_breaker_dd_pct:
            if not self.is_circuit_breaker_active:
                logger.warning(f"StreakRiskManager: Drawdown {current_dd:.1f}% >= {self.circuit_breaker_dd_pct:.1f}%. Circuit breaker TRIPPED -> Gear 1 locked.")
            self.is_circuit_breaker_active = True
            return 1

        if self.is_circuit_breaker_active:
            if current_dd <= self.recovery_dd_pct:
                logger.info(f"StreakRiskManager: Drawdown recovered to {current_dd:.1f}% <= {self.recovery_dd_pct:.1f}%. Circuit breaker RESET.")
                self.is_circuit_breaker_active = False
            else:
                return 1

        return self.current_gear

    def record_closed_trade(
        self,
        net_pnl: Decimal,
        initial_risk_usd: Optional[Decimal] = None,
        current_dd: Decimal = Decimal("0")
    ) -> int:
        """
        Processes trade outcome:
        - Solid Win (> 0.3 * 1R): streak_wins += 1, streak_losses = 0, gear steps up (+1)
        - Solid Loss (< -0.3 * 1R): streak_losses += 1, streak_wins = 0, gear steps down (-1)
        - Scratch / Flat (|PnL| <= 0.3 * 1R): neutral, no gear change
        """
        self.total_trades_processed += 1
        threshold = (initial_risk_usd * self.scratch_threshold_ratio) if (initial_risk_usd and initial_risk_usd > 0) else Decimal("1.50")

        prev_gear = self.current_gear

        if net_pnl > threshold:
            # Solid win
            self.streak_wins += 1
            self.streak_losses = 0
            if self.streak_wins == 1:
                self.current_gear = 2
            elif self.streak_wins >= 2:
                self.current_gear = 3
            logger.info(f"StreakRiskManager: Win recorded (+${net_pnl:.2f}). Streak wins: {self.streak_wins}. Gear: {prev_gear} -> {self.current_gear} ({self.get_config().name})")

        elif net_pnl < -threshold:
            # Solid loss
            self.streak_losses += 1
            self.streak_wins = 0
            # Step down smoothly (-1 gear)
            self.current_gear = max(1, self.current_gear - 1)
            logger.info(f"StreakRiskManager: Loss recorded (-${abs(net_pnl):.2f}). Streak losses: {self.streak_losses}. Gear: {prev_gear} -> {self.current_gear} ({self.get_config().name})")

        else:
            # Scratch / flat exit
            logger.info(f"StreakRiskManager: Neutral scratch trade (+${net_pnl:.2f} within threshold ±${threshold:.2f}). Gear remains {self.current_gear}.")

        # Check circuit breaker
        if current_dd >= self.circuit_breaker_dd_pct:
            self.is_circuit_breaker_active = True
            self.current_gear = 1

        return self.current_gear

    def calculate_position_size(
        self,
        equity: Decimal,
        entry_price: Decimal,
        stop_price: Decimal,
        min_order_qty: Decimal,
        qty_step: Decimal,
        min_notional: Decimal = Decimal("5.0"),
        current_dd: Decimal = Decimal("0"),
    ) -> StreakSizingResult:
        """
        Computes exact position size based on current gear's risk percentage.
        Enforces minOrderQty, qtyStep, minNotional, and max_leverage_cap.
        """
        if equity <= 0 or entry_price <= 0 or stop_price <= 0:
            return StreakSizingResult(False, Decimal("0"), Decimal("0"), Decimal("0"), 1, "Invalid inputs")

        distance = abs(entry_price - stop_price)
        if distance <= 0:
            return StreakSizingResult(False, Decimal("0"), Decimal("0"), Decimal("0"), 1, "Zero stop distance")

        gear = self.get_effective_gear(current_dd)
        cfg = GEAR_CONFIGS.get(gear, GEAR_CONFIGS[1])
        risk_pct = cfg.risk_pct

        target_risk_usd = equity * risk_pct
        raw_qty = target_risk_usd / distance

        # Max leverage clamp
        max_notional = equity * self.max_leverage_cap
        max_qty_by_lev = max_notional / entry_price
        raw_qty = min(raw_qty, max_qty_by_lev)

        # Quantize to qty_step
        if qty_step > 0:
            qty = (raw_qty / qty_step).to_integral_value(rounding=ROUND_FLOOR) * qty_step
        else:
            qty = raw_qty

        # Verify exchange rules
        if qty < min_order_qty:
            return StreakSizingResult(False, Decimal("0"), Decimal("0"), risk_pct, gear, f"Qty {qty} < minOrderQty {min_order_qty}")

        notional = qty * entry_price
        if notional < min_notional:
            return StreakSizingResult(False, Decimal("0"), Decimal("0"), risk_pct, gear, f"Notional ${notional:.2f} < minNotional ${min_notional:.2f}")

        actual_risk_usd = qty * distance
        return StreakSizingResult(
            allowed=True,
            qty=qty,
            risk_usd=actual_risk_usd,
            effective_risk_pct=risk_pct,
            gear=gear,
            reason="OK"
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "current_gear": self.current_gear,
            "streak_wins": self.streak_wins,
            "streak_losses": self.streak_losses,
            "total_trades_processed": self.total_trades_processed,
            "is_circuit_breaker_active": self.is_circuit_breaker_active,
            "gear_name": self.get_config().name,
            "risk_pct": float(self.get_config().risk_pct * 100),
            "rr_label": self.get_config().rr_label,
            "pyramiding_allowed": self.get_config().pyramiding_allowed,
            "pyramid_ratio": float(self.get_config().pyramid_ratio),
        }

    def from_dict(self, data: Dict[str, Any]) -> None:
        self.current_gear = int(data.get("current_gear", 1))
        self.streak_wins = int(data.get("streak_wins", 0))
        self.streak_losses = int(data.get("streak_losses", 0))
        self.total_trades_processed = int(data.get("total_trades_processed", 0))
        self.is_circuit_breaker_active = bool(data.get("is_circuit_breaker_active", False))
