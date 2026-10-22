"""
Fractional Kelly Position Sizing and Adaptive Regime Kill-Switch Module (v3.1).
Features:
- Half-Kelly baseline position sizing with strict Decimal arithmetic
- Multi-tier Auto-Gearbox (Gear 3: High Momentum, Gear 2: Middle Defensive, Gear 1: Low Capital Preservation)
- 50% Trough Recovery Auto-Upshift
- Kill-switch on consecutive losses and rolling low win rates
- Strict Bybit contract filter enforcement (minOrderQty, qtyStep, minNotional, maxLeverage)
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from typing import List, Optional


@dataclass
class KellyState:
    base_risk_pct: Decimal = Decimal("0.02")  # 2.0% Half-Kelly base risk
    choppy_risk_pct: Decimal = Decimal("0.006")  # 0.6% during regime kill-switch
    max_notional_leverage: Decimal = Decimal("3.5")  # 3.5x leverage cap
    recent_trade_results: List[bool] = None  # True for win, False for loss
    is_kill_switch_active: bool = False
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    current_gear: int = 3  # Auto-Gearbox: Gear 3 (High), Gear 2 (Middle), Gear 1 (Low)
    trough_equity: Decimal = Decimal("0")
    trough_dd_pct: Decimal = Decimal("0")

    def __post_init__(self):
        if self.recent_trade_results is None:
            self.recent_trade_results = []


@dataclass
class KellySizingResult:
    allowed: bool
    qty: Decimal
    effective_risk_pct: Decimal
    notional_value: Decimal
    stop_distance: Decimal
    skip_reason: Optional[str] = None


class KellyRiskManager:
    """
    Implements Half-Kelly position sizing with Auto-Gearbox drawdown protection.
    """

    def __init__(
        self,
        base_risk_pct: Decimal = Decimal("0.02"),
        choppy_risk_pct: Decimal = Decimal("0.006"),
        max_leverage: Decimal = Decimal("3.5"),
        loss_streak_threshold: int = 3,
        win_streak_recovery: int = 2,
        gearbox_down_threshold_1: Decimal = Decimal("10.0"),  # 10.0% DD -> Gear 2
        gearbox_down_threshold_2: Decimal = Decimal("16.0"),  # 16.0% DD -> Gear 1
        gearbox_recovery_ratio: Decimal = Decimal("0.5"),     # 50% recovery from trough -> upshift
    ):
        self.state = KellyState(
            base_risk_pct=base_risk_pct,
            choppy_risk_pct=choppy_risk_pct,
            max_notional_leverage=max_leverage,
        )
        self.loss_streak_threshold = loss_streak_threshold
        self.win_streak_recovery = win_streak_recovery
        self.gearbox_down_threshold_1 = gearbox_down_threshold_1
        self.gearbox_down_threshold_2 = gearbox_down_threshold_2
        self.gearbox_recovery_ratio = gearbox_recovery_ratio

    @property
    def current_gear(self) -> int:
        return self.state.current_gear

    @property
    def is_pyramiding_allowed(self) -> bool:
        """Pyramiding is strictly reserved for Gear 3 (high conviction / momentum)."""
        return self.state.current_gear == 3 and not self.state.is_kill_switch_active

    def get_effective_base_risk_pct(self) -> Decimal:
        """
        Dynamically adjusts risk percent based on current gearbox gear:
        Gear 3: 100% of base risk (e.g. 2.0%)
        Gear 2: 50% of base risk (e.g. 1.0%)
        Gear 1: 25% of base risk (e.g. 0.5%)
        """
        if self.state.current_gear == 3:
            return self.state.base_risk_pct
        elif self.state.current_gear == 2:
            return self.state.base_risk_pct / Decimal("2")
        else:  # Gear 1
            return self.state.base_risk_pct / Decimal("4")

    def update_drawdown(self, equity: Decimal, peak_equity: Decimal) -> int:
        """
        Updates Auto-Gearbox state based on peak equity, current equity, and trough recovery.
        Returns the updated current gear (1, 2, or 3).
        """
        if peak_equity <= Decimal("0") or equity <= Decimal("0"):
            return self.state.current_gear

        if equity >= peak_equity:
            # New peak reached: reset trough tracking and restore Gear 3
            self.state.trough_equity = equity
            self.state.trough_dd_pct = Decimal("0")
            self.state.current_gear = 3
            return 3

        dd_pct = (peak_equity - equity) / peak_equity * Decimal("100")

        # Update trough equity during drawdown
        if self.state.trough_equity <= Decimal("0") or equity < self.state.trough_equity:
            self.state.trough_equity = equity
            self.state.trough_dd_pct = dd_pct

        # Check downshift transitions
        if dd_pct >= self.gearbox_down_threshold_2:
            self.state.current_gear = 1
        elif dd_pct >= self.gearbox_down_threshold_1:
            if self.state.current_gear > 2:
                self.state.current_gear = 2

        # Check upshift transitions (50% recovery from trough towards peak)
        trough_loss = peak_equity - self.state.trough_equity
        recovery_from_trough = equity - self.state.trough_equity
        recovered_50pct = recovery_from_trough >= (trough_loss * self.gearbox_recovery_ratio)

        if self.state.current_gear == 1:
            if recovered_50pct or dd_pct < self.gearbox_down_threshold_1:
                self.state.current_gear = 2
        elif self.state.current_gear == 2:
            if dd_pct < Decimal("5.0"):
                self.state.current_gear = 3

        return self.state.current_gear

    def record_trade_result(self, is_win: bool) -> None:
        """Updates trade history and manages Kill-Switch state."""
        self.state.recent_trade_results.append(is_win)
        # Keep sliding window of 10 trades
        if len(self.state.recent_trade_results) > 10:
            self.state.recent_trade_results.pop(0)

        if is_win:
            self.state.consecutive_losses = 0
            self.state.consecutive_wins += 1
            if self.state.is_kill_switch_active:
                if self.state.consecutive_wins >= self.win_streak_recovery:
                    self.state.is_kill_switch_active = False
        else:
            self.state.consecutive_wins = 0
            self.state.consecutive_losses += 1
            if self.state.consecutive_losses >= self.loss_streak_threshold:
                self.state.is_kill_switch_active = True

        # Check rolling win rate over last 6 trades
        if len(self.state.recent_trade_results) >= 6:
            recent_6 = self.state.recent_trade_results[-6:]
            win_rate_6 = sum(1 for w in recent_6 if w) / len(recent_6)
            if win_rate_6 < 0.25:
                self.state.is_kill_switch_active = True

    def calculate_position_size(
        self,
        equity: Decimal,
        entry_price: Decimal,
        stop_price: Decimal,
        min_order_qty: Decimal,
        qty_step: Decimal,
        min_notional: Decimal = Decimal("5.0"),
    ) -> KellySizingResult:
        """
        Calculates position size using Auto-Gearbox risk and current regime kill-switch.
        """
        values = (equity, entry_price, stop_price, min_order_qty, qty_step, min_notional)
        if not all(v.is_finite() for v in values) or min(qty_step, min_order_qty, stop_price) <= 0 or min_notional < 0:
            return KellySizingResult(False, Decimal(0), Decimal(0), Decimal(0), Decimal(0), "Invalid instrument or non-finite value")
        if equity <= Decimal("0") or entry_price <= Decimal("0"):
            return KellySizingResult(
                allowed=False,
                qty=Decimal("0"),
                effective_risk_pct=Decimal("0"),
                notional_value=Decimal("0"),
                stop_distance=Decimal("0"),
                skip_reason="Invalid equity or price",
            )

        stop_dist = abs(entry_price - stop_price)
        if stop_dist <= Decimal("0"):
            return KellySizingResult(
                allowed=False,
                qty=Decimal("0"),
                effective_risk_pct=Decimal("0"),
                notional_value=Decimal("0"),
                stop_distance=Decimal("0"),
                skip_reason="Stop distance is zero",
            )

        # Select risk percentage based on kill switch vs Auto-Gearbox tier
        effective_base_risk = self.get_effective_base_risk_pct()
        current_risk_pct = (
            self.state.choppy_risk_pct
            if self.state.is_kill_switch_active
            else effective_base_risk
        )
        risk_budget = equity * current_risk_pct

        # Raw quantity from risk budget
        raw_qty = risk_budget / stop_dist

        # Cap by maximum notional leverage
        max_notional = equity * self.state.max_notional_leverage
        max_allowed_qty = max_notional / entry_price
        target_qty = min(raw_qty, max_allowed_qty)

        # Quantize down by qty_step
        quantized_qty = (target_qty / qty_step).quantize(
            Decimal("1"), rounding=ROUND_FLOOR
        ) * qty_step

        notional = quantized_qty * entry_price

        # Check Bybit filters
        if quantized_qty < min_order_qty:
            return KellySizingResult(
                allowed=False,
                qty=Decimal("0"),
                effective_risk_pct=current_risk_pct,
                notional_value=Decimal("0"),
                stop_distance=stop_dist,
                skip_reason=f"Qty {quantized_qty} below minOrderQty {min_order_qty}",
            )

        if notional < min_notional:
            return KellySizingResult(
                allowed=False,
                qty=Decimal("0"),
                effective_risk_pct=current_risk_pct,
                notional_value=notional,
                stop_distance=stop_dist,
                skip_reason=f"Notional {notional} below minNotional {min_notional}",
            )

        return KellySizingResult(
            allowed=True,
            qty=quantized_qty,
            effective_risk_pct=current_risk_pct,
            notional_value=notional,
            stop_distance=stop_dist,
            skip_reason=None,
        )
