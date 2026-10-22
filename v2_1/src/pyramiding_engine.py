"""
Anti-Martingale Pyramiding Engine v2.1.
Executes +50% position addition when price moves +2.0 ATR in favor.
Locks in Breakeven with Bybit MNT fee (0.09%) buffer, and maintains ratchet trailing stop.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
from typing import Optional


@dataclass
class ActivePosition:
    symbol: str
    direction: int  # 1 for Long, -1 for Short
    initial_qty: Decimal
    current_qty: Decimal
    entry_price: Decimal
    current_stop: Decimal
    initial_atr: Decimal
    highest_price: Decimal
    lowest_price: Decimal
    entry_time: int
    is_pyramided: bool = False
    is_breakeven_set: bool = False


@dataclass
class PyramidingAction:
    triggered: bool
    action_type: str  # "NONE", "PYRAMID_ADD", "STOP_UPDATE", "EXIT"
    added_qty: Decimal = Decimal("0")
    new_stop: Optional[Decimal] = None
    exit_reason: Optional[str] = None


class PyramidingEngine:
    def __init__(
        self,
        pyramid_trigger_atr_mult: Decimal = Decimal("2.0"),
        pyramid_add_ratio: Decimal = Decimal("0.5"),
        trailing_atr_mult: Decimal = Decimal("2.5"),
        fee_rate: Decimal = Decimal("0.0009"),      # Bybit with MNT discount: 0.09%
        slippage_rate: Decimal = Decimal("0.0002"), # 0.02%
    ):
        self.pyramid_trigger_atr_mult = pyramid_trigger_atr_mult
        self.pyramid_add_ratio = pyramid_add_ratio
        self.trailing_atr_mult = trailing_atr_mult
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate

    def evaluate_bar(
        self,
        pos: ActivePosition,
        current_high: Decimal,
        current_low: Decimal,
        current_close: Decimal,
        current_atr: Decimal,
        qty_step: Decimal,
        min_order_qty: Decimal,
    ) -> PyramidingAction:
        atr_entry = pos.initial_atr

        # 1. Update peak prices
        if current_high > pos.highest_price:
            pos.highest_price = current_high
        if current_low < pos.lowest_price:
            pos.lowest_price = current_low

        # 2. Check Stop-Loss hit (conservative worst-case check)
        if pos.direction == 1:
            if current_low <= pos.current_stop:
                return PyramidingAction(
                    triggered=True,
                    action_type="EXIT",
                    new_stop=pos.current_stop,
                    exit_reason="STOP_LOSS",
                )
        else:
            if current_high >= pos.current_stop:
                return PyramidingAction(
                    triggered=True,
                    action_type="EXIT",
                    new_stop=pos.current_stop,
                    exit_reason="STOP_LOSS",
                )

        # 3. Check Anti-Martingale Pyramiding trigger
        action_type = "NONE"
        added_qty = Decimal("0")

        if pos.direction == 1:
            favorable_dist = pos.highest_price - pos.entry_price
            if (
                not pos.is_pyramided
                and favorable_dist >= self.pyramid_trigger_atr_mult * atr_entry
            ):
                # Calculate buffer for breakeven (entry + round-trip fee + slippage)
                fee_buffer = (
                    Decimal("2") * self.fee_rate * pos.entry_price
                    + Decimal("2") * self.slippage_rate * pos.entry_price
                )
                be_stop = pos.entry_price + fee_buffer
                if be_stop > pos.current_stop:
                    pos.current_stop = be_stop
                pos.is_breakeven_set = True

                # Calculate 50% add-on quantity
                raw_add = pos.initial_qty * self.pyramid_add_ratio
                quantized_add = (raw_add / qty_step).quantize(
                    Decimal("1"), rounding=ROUND_FLOOR
                ) * qty_step

                if quantized_add >= min_order_qty:
                    added_qty = quantized_add
                    pos.current_qty += quantized_add
                    pos.is_pyramided = True
                    action_type = "PYRAMID_ADD"

            # Trailing stop update (ratchet upwards)
            trail_stop = pos.highest_price - self.trailing_atr_mult * current_atr
            if trail_stop > pos.current_stop:
                pos.current_stop = trail_stop
                if action_type == "NONE":
                    action_type = "STOP_UPDATE"

        else:
            # Short position
            favorable_dist = pos.entry_price - pos.lowest_price
            if (
                not pos.is_pyramided
                and favorable_dist >= self.pyramid_trigger_atr_mult * atr_entry
            ):
                fee_buffer = (
                    Decimal("2") * self.fee_rate * pos.entry_price
                    + Decimal("2") * self.slippage_rate * pos.entry_price
                )
                be_stop = pos.entry_price - fee_buffer
                if be_stop < pos.current_stop:
                    pos.current_stop = be_stop
                pos.is_breakeven_set = True

                raw_add = pos.initial_qty * self.pyramid_add_ratio
                quantized_add = (raw_add / qty_step).quantize(
                    Decimal("1"), rounding=ROUND_FLOOR
                ) * qty_step

                if quantized_add >= min_order_qty:
                    added_qty = quantized_add
                    pos.current_qty += quantized_add
                    pos.is_pyramided = True
                    action_type = "PYRAMID_ADD"

            # Trailing stop update (ratchet downwards)
            trail_stop = pos.lowest_price + self.trailing_atr_mult * current_atr
            if trail_stop < pos.current_stop:
                pos.current_stop = trail_stop
                if action_type == "NONE":
                    action_type = "STOP_UPDATE"

        return PyramidingAction(
            triggered=(action_type != "NONE"),
            action_type=action_type,
            added_qty=added_qty,
            new_stop=pos.current_stop,
        )
