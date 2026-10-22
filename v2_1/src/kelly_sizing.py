"""
Fractional Kelly Position Sizing and Adaptive Regime Kill-Switch Module.
Strict adherence to SPEC_V2.md Section 3.2 & 3.4.
Uses Decimal arithmetic for precise risk management.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from typing import List, Optional


@dataclass
class KellyState:
    base_risk_pct: Decimal = Decimal("0.025")  # 2.5% Half-Kelly base risk
    choppy_risk_pct: Decimal = Decimal("0.0075")  # 0.75% during regime kill-switch
    max_notional_leverage: Decimal = Decimal("3.5")  # 3.5x leverage cap
    recent_trade_results: List[bool] = None  # True for win, False for loss
    is_kill_switch_active: bool = False
    consecutive_losses: int = 0
    consecutive_wins: int = 0

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
    Implements Half-Kelly position sizing with dynamic regime detection.
    """

    def __init__(
        self,
        base_risk_pct: Decimal = Decimal("0.025"),
        choppy_risk_pct: Decimal = Decimal("0.0075"),
        max_leverage: Decimal = Decimal("3.5"),
        loss_streak_threshold: int = 3,
        win_streak_recovery: int = 2,
    ):
        self.state = KellyState(
            base_risk_pct=base_risk_pct,
            choppy_risk_pct=choppy_risk_pct,
            max_notional_leverage=max_leverage,
        )
        self.loss_streak_threshold = loss_streak_threshold
        self.win_streak_recovery = win_streak_recovery

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
        Calculates position size using fractional Kelly and current regime risk.
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

        # Select risk percentage based on regime
        current_risk_pct = (
            self.state.choppy_risk_pct
            if self.state.is_kill_switch_active
            else self.state.base_risk_pct
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
