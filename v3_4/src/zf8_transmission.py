"""
ZF-8 Intelligent Quant Transmission Engine for Multi-Alpha v3.4.
Provides 8-Speed adaptive risk gearing, dynamic R:R cascade take-profit targets,
and non-linear intelligent gear shifting (Smart Kickdown & ABS Rapid Braking).

Gears Specification:
- Gear 1 (Limp Mode / Crawl): Risk 1.0%, R:R 1:2.8 - 1:3.6, Pyramid OFF
- Gear 2 (Base City):         Risk 1.3%, R:R 1:3.0 - 1:4.0, Pyramid +20%
- Gear 3 (Steady Cruise):     Risk 1.6%, R:R 1:3.3 - 1:4.4, Pyramid +35%
- Gear 4 (Drive / Mid):       Risk 2.0%, R:R 1:3.6 - 1:4.8, Pyramid +50%
- Gear 5 (Fast Lane):         Risk 2.4%, R:R 1:4.0 - 1:5.2, Pyramid +65%
- Gear 6 (Sport):             Risk 2.8%, R:R 1:4.4 - 1:5.6, Pyramid +75%
- Gear 7 (Sport+):            Risk 3.2%, R:R 1:4.8 - 1:6.0, Pyramid +85%
- Gear 8 (Launch / Overdrive):Risk 3.6%, R:R 1:5.2 - 1:6.5, Pyramid +100%

Non-Linear Shifting Dynamics:
1. Kickdown Upshift:
   - Mega-Runner (R >= 5.0R): +3 Gears immediately (e.g. G3 -> G6, G4 -> G7)
   - Super-Runner (3.2R <= R < 5.0R): +2 Gears immediately (e.g. G2 -> G4, G5 -> G7)
   - Standard Win (0.3R <= R < 3.2R): +1 Gear sequentially
2. ABS Rapid Braking Downshift:
   - Full Stop-Loss (R < -0.7R): -2 Gears down immediately (e.g. G6 -> G4, G4 -> G2)
   - Soft Loss / Cut (-0.7R <= R <= -0.3R): -1 Gear down
3. Scratch / Breakeven (|R| < 0.3R):
   - Neutral (gear preserved, streak preserved)
4. Limp Home Mode (Circuit Breaker):
   - If Drawdown >= 8.0%: Locks into Gear 1 (1.0% risk) until DD recovers <= 4.0%.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("ZF8Transmission")


@dataclass
class GearConfig:
    gear: int
    name: str
    tag: str
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
        name="LIMP / CRAWL",
        tag="G1",
        risk_pct=Decimal("0.010"),        # 1.0% defensive risk
        tp1_r_mult=Decimal("2.8"),        # TP1 at +2.8R
        tp2_r_mult=Decimal("3.6"),        # TP2 at +3.6R
        tp1_ratio=Decimal("0.35"),
        tp2_ratio=Decimal("0.35"),
        pyramiding_allowed=False,
        pyramid_ratio=Decimal("0.0"),
        rr_label="1:2.8 — 1:3.6",
    ),
    2: GearConfig(
        gear=2,
        name="BASE CITY",
        tag="G2",
        risk_pct=Decimal("0.013"),        # 1.3% base entry
        tp1_r_mult=Decimal("3.0"),
        tp2_r_mult=Decimal("4.0"),
        tp1_ratio=Decimal("0.35"),
        tp2_ratio=Decimal("0.35"),
        pyramiding_allowed=True,
        pyramid_ratio=Decimal("0.20"),    # +20% add at +2.0 ATR
        rr_label="1:3.0 — 1:4.0",
    ),
    3: GearConfig(
        gear=3,
        name="STEADY CRUISE",
        tag="G3",
        risk_pct=Decimal("0.016"),        # 1.6% cruise
        tp1_r_mult=Decimal("3.3"),
        tp2_r_mult=Decimal("4.4"),
        tp1_ratio=Decimal("0.35"),
        tp2_ratio=Decimal("0.35"),
        pyramiding_allowed=True,
        pyramid_ratio=Decimal("0.35"),    # +35% add
        rr_label="1:3.3 — 1:4.4",
    ),
    4: GearConfig(
        gear=4,
        name="DRIVE (MID)",
        tag="G4",
        risk_pct=Decimal("0.020"),        # 2.0% drive standard
        tp1_r_mult=Decimal("3.6"),
        tp2_r_mult=Decimal("4.8"),
        tp1_ratio=Decimal("0.35"),
        tp2_ratio=Decimal("0.35"),
        pyramiding_allowed=True,
        pyramid_ratio=Decimal("0.50"),    # +50% add
        rr_label="1:3.6 — 1:4.8",
    ),
    5: GearConfig(
        gear=5,
        name="FAST LANE",
        tag="G5",
        risk_pct=Decimal("0.024"),        # 2.4% accelerated
        tp1_r_mult=Decimal("4.0"),
        tp2_r_mult=Decimal("5.2"),
        tp1_ratio=Decimal("0.30"),
        tp2_ratio=Decimal("0.35"),
        pyramiding_allowed=True,
        pyramid_ratio=Decimal("0.65"),    # +65% add
        rr_label="1:4.0 — 1:5.2",
    ),
    6: GearConfig(
        gear=6,
        name="SPORT",
        tag="G6",
        risk_pct=Decimal("0.028"),        # 2.8% sport
        tp1_r_mult=Decimal("4.4"),
        tp2_r_mult=Decimal("5.6"),
        tp1_ratio=Decimal("0.30"),
        tp2_ratio=Decimal("0.35"),
        pyramiding_allowed=True,
        pyramid_ratio=Decimal("0.75"),    # +75% add
        rr_label="1:4.4 — 1:5.6",
    ),
    7: GearConfig(
        gear=7,
        name="SPORT+",
        tag="G7",
        risk_pct=Decimal("0.032"),        # 3.2% sport+
        tp1_r_mult=Decimal("4.8"),
        tp2_r_mult=Decimal("6.0"),
        tp1_ratio=Decimal("0.30"),
        tp2_ratio=Decimal("0.35"),
        pyramiding_allowed=True,
        pyramid_ratio=Decimal("0.85"),    # +85% add
        rr_label="1:4.8 — 1:6.0",
    ),
    8: GearConfig(
        gear=8,
        name="LAUNCH CONTROL",
        tag="G8",
        risk_pct=Decimal("0.036"),        # 3.6% launch control / overdrive
        tp1_r_mult=Decimal("5.2"),
        tp2_r_mult=Decimal("6.5"),
        tp1_ratio=Decimal("0.30"),
        tp2_ratio=Decimal("0.35"),
        pyramiding_allowed=True,
        pyramid_ratio=Decimal("1.00"),    # 100% (double position size on super-trend)
        rr_label="1:5.2 — 1:6.5",
    ),
}


@dataclass
class TransmissionSizingResult:
    allowed: bool
    qty: Decimal
    risk_usd: Decimal
    effective_risk_pct: Decimal
    gear: int
    reason: str = ""


class ZF8Transmission:
    """
    Intelligent 8-Speed Quantitative Transmission.
    Manages non-linear multi-gear shifts, kickdown acceleration, ABS rapid braking,
    and adaptive position sizing with penny-perfect invariants.
    """

    def __init__(
        self,
        initial_capital: Decimal = Decimal("300.0"),
        initial_gear: int = 1,
        max_leverage_cap: Decimal = Decimal("4.0"),
        circuit_breaker_dd_pct: Decimal = Decimal("8.0"),
        recovery_dd_pct: Decimal = Decimal("4.0"),
        scratch_threshold_ratio: Decimal = Decimal("0.30"),
    ):
        self.initial_capital = initial_capital
        self.current_gear = max(1, min(8, initial_gear))
        self.streak_wins = 0
        self.streak_losses = 0
        self.total_trades_processed = 0
        self.max_leverage_cap = max_leverage_cap
        self.circuit_breaker_dd_pct = circuit_breaker_dd_pct
        self.recovery_dd_pct = recovery_dd_pct
        self.scratch_threshold_ratio = scratch_threshold_ratio
        self.is_circuit_breaker_active = False
        self.last_shift_reason = "INITIAL_STATE"

    def get_config(self) -> GearConfig:
        """Returns the GearConfig for the active gear."""
        return GEAR_CONFIGS.get(self.current_gear, GEAR_CONFIGS[1])

    @property
    def current_config(self) -> GearConfig:
        """Alias property for get_config()."""
        return self.get_config()

    @property
    def in_limp_mode(self) -> bool:
        """Alias property for is_circuit_breaker_active."""
        return self.is_circuit_breaker_active

    def get_effective_gear(self, current_dd: Decimal = Decimal("0")) -> int:
        """
        Returns active gear considering drawdown circuit breaker:
        If current_dd >= 8.0%, locks into Gear 1 (Limp Mode).
        """
        if current_dd >= self.circuit_breaker_dd_pct:
            if not self.is_circuit_breaker_active:
                logger.warning(
                    f"ZF8Transmission: Drawdown {current_dd:.1f}% >= {self.circuit_breaker_dd_pct:.1f}%. "
                    f"Limp Mode TRIPPED -> Locked in Gear 1 (1.0% Risk)."
                )
            self.is_circuit_breaker_active = True
            return 1

        if self.is_circuit_breaker_active:
            if current_dd <= self.recovery_dd_pct:
                logger.info(
                    f"ZF8Transmission: Drawdown recovered to {current_dd:.1f}% <= {self.recovery_dd_pct:.1f}%. "
                    f"Limp Mode RESET -> Restoring transmission to Gear 2 (Base City) to prevent shadow gear shock."
                )
                self.is_circuit_breaker_active = False
                self.current_gear = 2  # Hard reset to safe Gear 2
            else:
                return 1

        return self.current_gear

    def record_closed_trade(
        self,
        net_pnl: Decimal,
        initial_risk_usd: Optional[Decimal] = None,
        current_dd: Decimal = Decimal("0"),
        current_equity: Optional[Decimal] = None,
    ) -> int:
        """
        Processes closed trade outcome with non-linear shift logic:
        - R >= 5.0: Mega Kickdown (+3 gears)
        - 3.2 <= R < 5.0: Super Kickdown (+2 gears)
        - 0.3 <= R < 3.2: Sequential Upshift (+1 gear)
        - -0.3 <= R < 0.3: Neutral / Scratch (0 gears)
        - -0.7 <= R < -0.3: Gentle Downshift (-1 gear)
        - R < -0.7: ABS Rapid Braking (-2 gears)
        """
        self.total_trades_processed += 1
        if initial_risk_usd and initial_risk_usd > 0:
            risk_base = initial_risk_usd
        elif current_equity and current_equity > 0:
            risk_base = max(Decimal("1.00"), current_equity * Decimal("0.01"))
        else:
            risk_base = Decimal("3.00")
        r_mult = net_pnl / risk_base
        prev_gear = self.current_gear

        if r_mult >= Decimal("0.30"):
            # WIN
            self.streak_wins += 1
            self.streak_losses = 0

            if current_dd > Decimal("3.5"):
                # Ban kickdown (+2/+3) while in notable drawdown (> 3.5%)
                shift = 1
                self.last_shift_reason = f"CAUTIOUS_UPSHIFT (+1G, DD={current_dd:.1f}% > 3.5%, R={r_mult:.1f})"
            elif r_mult >= Decimal("5.0"):
                # Mega-Runner (+3 gears)
                shift = 3
                self.last_shift_reason = f"MEGA_KICKDOWN (+3G, R={r_mult:.1f})"
            elif r_mult >= Decimal("3.2"):
                # Super-Runner (+2 gears)
                shift = 2
                self.last_shift_reason = f"KICKDOWN (+2G, R={r_mult:.1f})"
            else:
                # Standard Win (+1 gear)
                shift = 1
                self.last_shift_reason = f"UPSHIFT (+1G, R={r_mult:.1f})"

            self.current_gear = min(8, self.current_gear + shift)
            logger.info(
                f"ZF8Transmission: Win recorded (+${net_pnl:.2f}, {r_mult:.2f}R). "
                f"Streak wins: {self.streak_wins}. Gear: G{prev_gear} -> G{self.current_gear} ({self.get_config().name}). "
                f"Reason: {self.last_shift_reason}"
            )

        elif r_mult <= Decimal("-0.30"):
            # LOSS
            self.streak_losses += 1
            self.streak_wins = 0

            if r_mult < Decimal("-0.70"):
                # Full Stop Loss -> ABS Rapid Braking (-2 gears down)
                shift = -2
                self.last_shift_reason = f"ABS_BRAKING (-2G, R={r_mult:.1f})"
            else:
                # Soft Loss / Scratch cut (-1 gear down)
                shift = -1
                self.last_shift_reason = f"DOWNSHIFT (-1G, R={r_mult:.1f})"

            self.current_gear = max(1, self.current_gear + shift)
            logger.info(
                f"ZF8Transmission: Loss recorded (-${abs(net_pnl):.2f}, {r_mult:.2f}R). "
                f"Streak losses: {self.streak_losses}. Gear: G{prev_gear} -> G{self.current_gear} ({self.get_config().name}). "
                f"Reason: {self.last_shift_reason}"
            )

        else:
            # Neutral Scratch
            self.last_shift_reason = f"NEUTRAL_SCRATCH (R={r_mult:.2f})"
            logger.info(
                f"ZF8Transmission: Neutral scratch (+${net_pnl:.2f}, {r_mult:.2f}R). "
                f"Transmission in neutral, maintaining Gear G{self.current_gear}."
            )

        # Drawdown check
        if current_dd >= self.circuit_breaker_dd_pct:
            self.is_circuit_breaker_active = True
            self.last_shift_reason = f"LIMP_MODE_TRIPPED (DD={current_dd:.1f}%)"

        return self.get_effective_gear(current_dd)

    def calculate_sizing(
        self,
        equity: Decimal,
        price: Decimal,
        stop_loss_distance: Decimal,
        qty_step: Decimal,
        min_order_qty: Decimal,
        min_notional: Decimal,
        current_dd: Decimal = Decimal("0"),
        active_high_gear_positions: int = 0,
    ) -> TransmissionSizingResult:
        """
        Calculates position size strictly constrained by active transmission gear (1-8).
        Clamps to Gear 4 if another position is already running on a high gear (G6-G8).
        """
        if equity <= Decimal("0") or price <= Decimal("0") or stop_loss_distance <= Decimal("0"):
            return TransmissionSizingResult(False, Decimal("0"), Decimal("0"), Decimal("0"), 1, "Invalid market inputs")

        gear = self.get_effective_gear(current_dd)
        # Concentration rule: max 1 position in G6-G8 concurrently
        if active_high_gear_positions >= 1 and gear >= 6:
            gear = 4
            logger.info("ZF8Transmission: Max 1 position allowed in G6-G8. Clamping entry to Gear 4 (2.0% risk).")

        cfg = GEAR_CONFIGS.get(gear, GEAR_CONFIGS[1])
        risk_pct = cfg.risk_pct

        risk_usd = equity * risk_pct
        raw_qty = risk_usd / stop_loss_distance

        # Leverage cap
        max_notional = equity * self.max_leverage_cap
        max_qty_by_leverage = max_notional / price
        raw_qty = min(raw_qty, max_qty_by_leverage)

        # Quantize to step
        if qty_step > Decimal("0"):
            steps = (raw_qty / qty_step).quantize(Decimal("1"), rounding=ROUND_FLOOR)
            qty = steps * qty_step
        else:
            qty = raw_qty

        notional = qty * price
        if qty < min_order_qty:
            return TransmissionSizingResult(
                False, Decimal("0"), Decimal("0"), risk_pct, gear,
                f"Qty {qty} < minOrderQty {min_order_qty}"
            )

        if notional < min_notional:
            return TransmissionSizingResult(
                False, Decimal("0"), Decimal("0"), risk_pct, gear,
                f"Notional ${notional:.2f} < minNotional ${min_notional:.2f}"
            )

        return TransmissionSizingResult(
            allowed=True,
            qty=qty,
            risk_usd=risk_usd,
            effective_risk_pct=risk_pct,
            gear=gear,
            reason=f"Approved on G{gear} ({cfg.name})"
        )

    def to_dict(self) -> Dict[str, Any]:
        """Serializes transmission telemetry for live dashboard and meta-aggregator."""
        cfg = self.get_config()
        return {
            "current_gear": self.current_gear,
            "gear_name": cfg.name,
            "gear_tag": cfg.tag,
            "risk_pct": float(cfg.risk_pct * Decimal("100")),
            "rr_label": cfg.rr_label,
            "tp1_r": float(cfg.tp1_r_mult),
            "tp2_r": float(cfg.tp2_r_mult),
            "pyramiding_allowed": cfg.pyramiding_allowed,
            "pyramid_ratio": float(cfg.pyramid_ratio),
            "streak_wins": self.streak_wins,
            "streak_losses": self.streak_losses,
            "total_trades": self.total_trades_processed,
            "is_circuit_breaker_active": self.is_circuit_breaker_active,
            "last_shift_reason": self.last_shift_reason,
            "all_gears": [
                {
                    "gear": g.gear,
                    "name": g.name,
                    "tag": g.tag,
                    "risk_pct": float(g.risk_pct * Decimal("100")),
                    "rr_label": g.rr_label,
                    "pyramid_pct": int(g.pyramid_ratio * 100),
                }
                for g in GEAR_CONFIGS.values()
            ],
        }

    def load_dict(self, data: Dict[str, Any]) -> None:
        """Restores state from disk snapshot."""
        if not data:
            return
        self.current_gear = max(1, min(8, int(data.get("current_gear", 1))))
        self.streak_wins = int(data.get("streak_wins", 0))
        self.streak_losses = int(data.get("streak_losses", 0))
        self.total_trades_processed = int(data.get("total_trades", 0))
        self.is_circuit_breaker_active = bool(data.get("is_circuit_breaker_active", False))
        self.last_shift_reason = str(data.get("last_shift_reason", "RESTORED_FROM_DISK"))
