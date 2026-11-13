"""
src/capital_ledger_010r.py

Finite Isolated Capital Ledger & Margin Risk Controller for Bybit V5 Perpetual Fleet.
Enforces institutional accounting invariants:
1. Initial Equity = $300.00.
2. Dynamic equity-scaled risk sizing: risk_usd = max(0, equity) * risk_pct.
3. Mark-to-market continuous equity tracking.
4. Pre-trade margin availability and capacity verification.
5. Max Drawdown kill-switch (30% drawdown halts new entries).
6. Isolated account solvency floor: zero phantom debt (equity cannot drop below 0).
7. Single-fee deduction per fill via FeeContract009B.
8. Empirical 8h funding settlement at 00:00, 08:00, 16:00 UTC.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
import logging
from typing import Dict, List, Optional, Tuple, Any

from src.fee_contract_009b import (
    CONSERVATIVE_OPERATOR_PROFILE,
    FeeProfile,
    calculate_funding_cashflow,
)

logger = logging.getLogger("CapitalLedger010R")


@dataclass
class LedgerPosition:
    symbol: str
    direction: int  # +1 Long, -1 Short
    entry_price: Decimal
    current_qty: Decimal
    initial_qty: Decimal
    stop_loss: Decimal
    entry_fee: Decimal
    entry_ts: int
    unrealized_pnl: Decimal = Decimal("0.00")
    accumulated_funding: Decimal = Decimal("0.00")
    is_pyramided: bool = False
    stage: int = 0
    tp_maker_filled: bool = False


@dataclass
class TradeRecord:
    trade_id: str
    symbol: str
    direction: int
    entry_ts: int
    exit_ts: int
    entry_price: Decimal
    exit_price: Decimal
    qty: Decimal
    gross_pnl: Decimal
    entry_fee: Decimal
    exit_fee: Decimal
    funding_pnl: Decimal
    net_pnl: Decimal
    exit_reason: str
    hold_hours: float
    trade_notional: Decimal


@dataclass
class EquityPoint:
    timestamp: int
    equity: Decimal
    cash: Decimal
    unrealized_pnl: Decimal
    margin_used: Decimal
    available_margin: Decimal
    drawdown_pct: Decimal
    is_halted: bool


class IsolatedCapitalLedger:
    """
    Stateful accounting ledger and pre-trade margin validator.
    Guarantees finite isolated capital accounting without zombie margin debt.
    """

    def __init__(
        self,
        initial_capital: Decimal = Decimal("300.00"),
        max_leverage: Decimal = Decimal("3.5"),
        maintenance_margin_rate: Decimal = Decimal("0.01"),  # 1.0% Bybit maintenance margin
        drawdown_limit_pct: Decimal = Decimal("30.0"),       # 30.0% max drawdown halt
        fee_profile: FeeProfile = CONSERVATIVE_OPERATOR_PROFILE,
        min_notional_default: Decimal = Decimal("5.00"),
    ):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.realized_equity = initial_capital
        self.peak_equity = initial_capital
        self.max_leverage = max_leverage
        self.maintenance_margin_rate = maintenance_margin_rate
        self.drawdown_limit_pct = drawdown_limit_pct
        self.fee_profile = fee_profile
        self.min_notional_default = min_notional_default

        self.positions: Dict[str, LedgerPosition] = {}
        self.closed_trades: List[TradeRecord] = []
        self.equity_curve: List[EquityPoint] = []

        self.total_fees_paid: Decimal = Decimal("0.00")
        self.total_funding_pnl: Decimal = Decimal("0.00")
        self.total_gross_pnl: Decimal = Decimal("0.00")
        self.total_net_pnl: Decimal = Decimal("0.00")

        self.is_halted: bool = False
        self.halt_reason: Optional[str] = None
        self.max_drawdown_observed_pct: Decimal = Decimal("0.00")
        self.max_drawdown_observed_usd: Decimal = Decimal("0.00")

    def get_equity(self) -> Decimal:
        """Returns total current mark-to-market equity."""
        unrealized = sum(p.unrealized_pnl for p in self.positions.values())
        eq = self.cash + unrealized
        # Solvency floor on isolated account: cannot be negative
        return max(Decimal("0.00"), eq)

    def get_margin_used(self) -> Decimal:
        """Total initial margin required across all active positions."""
        total_notional = Decimal("0.00")
        for p in self.positions.values():
            total_notional += p.current_qty * p.entry_price
        return total_notional / self.max_leverage if self.max_leverage > 0 else Decimal("0.00")

    def get_maintenance_margin(self) -> Decimal:
        """Total maintenance margin across all active positions."""
        total_notional = Decimal("0.00")
        for p in self.positions.values():
            total_notional += p.current_qty * p.entry_price
        return total_notional * self.maintenance_margin_rate

    def get_available_margin(self) -> Decimal:
        """Available margin for opening new positions."""
        if self.is_halted:
            return Decimal("0.00")
        eq = self.get_equity()
        used = self.get_margin_used()
        return max(Decimal("0.00"), eq - used)

    def mark_to_market(self, timestamp: int, marks: Dict[str, Decimal]) -> EquityPoint:
        """
        Updates unrealized PnL and records continuous equity point.
        Enforces drawdown kill-switch and liquidation solvency floor.
        """
        for sym, pos in self.positions.items():
            if sym in marks:
                mark_px = marks[sym]
                pos.unrealized_pnl = pos.current_qty * (mark_px - pos.entry_price) * pos.direction

        total_unrealized = sum(p.unrealized_pnl for p in self.positions.values())
        raw_equity = self.cash + total_unrealized

        # Check liquidation / equity exhaustion
        maint_margin = self.get_maintenance_margin()
        if raw_equity <= maint_margin and len(self.positions) > 0 and not self.is_halted:
            self.is_halted = True
            self.halt_reason = "MAINTENANCE_MARGIN_LIQUIDATION"
            raw_equity = Decimal("0.00")
        elif raw_equity <= Decimal("0.00") and not self.is_halted:
            self.is_halted = True
            self.halt_reason = "EQUITY_EXHAUSTION"
            raw_equity = Decimal("0.00")

        current_equity = max(Decimal("0.00"), raw_equity)

        # Update peak equity only if not halted and current_equity > peak
        if not self.is_halted and current_equity > self.peak_equity:
            self.peak_equity = current_equity

        # Compute drawdown from peak
        if self.peak_equity > Decimal("0.00"):
            dd_usd = self.peak_equity - current_equity
            dd_pct = (dd_usd / self.peak_equity) * Decimal("100.0")
        else:
            dd_usd = Decimal("0.00")
            dd_pct = Decimal("0.00")

        # Cap max drawdown at 100.0% for isolated account
        dd_pct = min(Decimal("100.00"), dd_pct)
        if dd_pct > self.max_drawdown_observed_pct:
            self.max_drawdown_observed_pct = dd_pct
        if dd_usd > self.max_drawdown_observed_usd:
            self.max_drawdown_observed_usd = dd_usd

        # Check Drawdown Limit Kill-Switch
        if dd_pct >= self.drawdown_limit_pct and not self.is_halted:
            self.is_halted = True
            self.halt_reason = f"DRAWDOWN_LIMIT_HALT_{dd_pct:.2f}%"

        eq_pt = EquityPoint(
            timestamp=timestamp,
            equity=current_equity,
            cash=self.cash,
            unrealized_pnl=total_unrealized,
            margin_used=self.get_margin_used(),
            available_margin=self.get_available_margin(),
            drawdown_pct=dd_pct,
            is_halted=self.is_halted,
        )
        self.equity_curve.append(eq_pt)
        return eq_pt

    def check_new_order(
        self,
        symbol: str,
        price: Decimal,
        qty: Decimal,
        min_notional: Optional[Decimal] = None,
        max_order_qty: Optional[Decimal] = None,
        is_reduce_only: bool = False,
    ) -> Tuple[bool, str]:
        """
        Pre-trade margin, capacity, and risk authorization gate.
        Executed strictly BEFORE accepting or executing any order.
        """
        if self.is_halted and not is_reduce_only:
            return False, f"REJECTED_TRADING_HALTED: {self.halt_reason}"

        notional = price * qty
        min_not = min_notional or self.min_notional_default
        if notional < min_not:
            return False, f"REJECTED_BELOW_MIN_NOTIONAL: {notional} < {min_not}"

        if max_order_qty is not None and qty > max_order_qty:
            return False, f"REJECTED_EXCEEDS_MAX_QTY: {qty} > {max_order_qty}"

        if not is_reduce_only:
            req_margin = notional / self.max_leverage
            avail_margin = self.get_available_margin()
            if req_margin > avail_margin:
                return False, f"REJECTED_INSUFFICIENT_MARGIN: req={req_margin:.2f} > avail={avail_margin:.2f}"

        return True, "AUTHORIZED"

    def calculate_risk_qty(
        self,
        price: Decimal,
        stop_dist: Decimal,
        risk_pct: Decimal,
        qty_step: Decimal = Decimal("0.001"),
        min_qty: Decimal = Decimal("0.001"),
        min_notional: Optional[Decimal] = None,
    ) -> Tuple[Decimal, Decimal, str]:
        """
        Calculates position quantity dynamically scaled by actual current equity.
        Returns: (qty, risk_usd, status)
        """
        if self.is_halted:
            return Decimal("0.00"), Decimal("0.00"), f"REJECTED_HALTED: {self.halt_reason}"

        curr_eq = self.get_equity()
        if curr_eq <= Decimal("0.00"):
            return Decimal("0.00"), Decimal("0.00"), "REJECTED_ZERO_EQUITY"

        risk_usd = curr_eq * risk_pct
        if stop_dist <= Decimal("0.00"):
            return Decimal("0.00"), Decimal("0.00"), "REJECTED_INVALID_STOP_DIST"

        raw_qty = risk_usd / stop_dist

        # Leverage and available margin cap
        avail_margin = self.get_available_margin()
        max_notional = avail_margin * self.max_leverage
        max_qty_by_margin = max_notional / price if price > Decimal("0.00") else Decimal("0.00")

        target_qty = min(raw_qty, max_qty_by_margin)

        # Quantize to qty_step
        if qty_step > Decimal("0.00"):
            quantized_qty = (target_qty / qty_step).to_integral_value(rounding=ROUND_FLOOR) * qty_step
        else:
            quantized_qty = target_qty

        if quantized_qty < min_qty:
            return Decimal("0.00"), Decimal("0.00"), f"REJECTED_BELOW_MIN_QTY: {quantized_qty} < {min_qty}"

        min_not = min_notional or self.min_notional_default
        if (quantized_qty * price) < min_not:
            return Decimal("0.00"), Decimal("0.00"), f"REJECTED_BELOW_MIN_NOTIONAL: {quantized_qty * price} < {min_not}"

        actual_risk_usd = quantized_qty * stop_dist
        return quantized_qty, actual_risk_usd, "OK"

    def open_position(
        self,
        symbol: str,
        direction: int,
        entry_price: Decimal,
        qty: Decimal,
        stop_loss: Decimal,
        timestamp: int,
        is_maker: bool = False,
    ) -> LedgerPosition:
        """Opens a new position, deducts entry fee from cash, and tracks position."""
        fee_rate = self.fee_profile.maker_rate if is_maker else self.fee_profile.taker_rate
        entry_notional = entry_price * qty
        entry_fee = entry_notional * fee_rate

        self.cash -= entry_fee
        self.total_fees_paid += entry_fee

        pos = LedgerPosition(
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            current_qty=qty,
            initial_qty=qty,
            stop_loss=stop_loss,
            entry_fee=entry_fee,
            entry_ts=timestamp,
            unrealized_pnl=Decimal("0.00"),
            accumulated_funding=Decimal("0.00"),
        )
        self.positions[symbol] = pos
        return pos

    def add_to_position(
        self,
        symbol: str,
        add_price: Decimal,
        add_qty: Decimal,
        new_stop: Decimal,
        timestamp: int,
        is_maker: bool = False,
    ) -> LedgerPosition:
        """
        Anti-martingale pyramiding add-on:
        Updates weighted average entry price penny-perfectly and deducts fee.
        """
        pos = self.positions[symbol]
        fee_rate = self.fee_profile.maker_rate if is_maker else self.fee_profile.taker_rate
        add_notional = add_price * add_qty
        add_fee = add_notional * fee_rate

        self.cash -= add_fee
        self.total_fees_paid += add_fee

        old_notional = pos.current_qty * pos.entry_price
        total_qty = pos.current_qty + add_qty
        weighted_entry = (old_notional + add_notional) / total_qty

        pos.entry_price = weighted_entry
        pos.current_qty = total_qty
        pos.entry_fee += add_fee
        pos.stop_loss = new_stop
        pos.is_pyramided = True
        return pos

    def partial_close(
        self,
        symbol: str,
        close_price: Decimal,
        close_qty: Decimal,
        timestamp: int,
        reason: str,
        is_maker: bool = True,
    ) -> TradeRecord:
        """
        Executes a partial take-profit (e.g. Stage 2 60% Maker TP).
        Conserves cashflow: credits realized profit, deducts exit fee.
        """
        pos = self.positions[symbol]
        direction = pos.direction
        fee_rate = self.fee_profile.maker_rate if is_maker else self.fee_profile.taker_rate
        notional = close_price * close_qty
        exit_fee = notional * fee_rate

        # Allocated portion of entry fee and funding
        fraction = close_qty / pos.initial_qty
        alloc_entry_fee = pos.entry_fee * fraction
        alloc_funding = pos.accumulated_funding * (close_qty / pos.current_qty) if pos.current_qty > 0 else Decimal("0.00")

        gross_pnl = close_qty * (close_price - pos.entry_price) * direction
        net_pnl = gross_pnl - alloc_entry_fee - exit_fee + alloc_funding

        # Cashflow update: funding was already credited/debited at settlement
        self.cash += (gross_pnl - exit_fee)
        self.total_fees_paid += exit_fee
        self.total_gross_pnl += gross_pnl
        self.total_net_pnl += net_pnl

        # Update remaining position
        pos.current_qty -= close_qty
        pos.entry_fee -= alloc_entry_fee
        pos.accumulated_funding -= alloc_funding
        pos.tp_maker_filled = True

        trade = TradeRecord(
            trade_id=f"TR_{symbol}_{timestamp}_PARTIAL",
            symbol=symbol,
            direction=direction,
            entry_ts=pos.entry_ts,
            exit_ts=timestamp,
            entry_price=pos.entry_price,
            exit_price=close_price,
            qty=close_qty,
            gross_pnl=gross_pnl,
            entry_fee=alloc_entry_fee,
            exit_fee=exit_fee,
            funding_pnl=alloc_funding,
            net_pnl=net_pnl,
            exit_reason=reason,
            hold_hours=(timestamp - pos.entry_ts) / 3600000.0,
            trade_notional=notional,
        )
        self.closed_trades.append(trade)
        return trade

    def close_position(
        self,
        symbol: str,
        exit_price: Decimal,
        timestamp: int,
        reason: str,
        is_maker: bool = False,
    ) -> TradeRecord:
        """Full exit of remaining position."""
        pos = self.positions[symbol]
        direction = pos.direction
        qty = pos.current_qty
        fee_rate = self.fee_profile.maker_rate if is_maker else self.fee_profile.taker_rate
        notional = exit_price * qty
        exit_fee = notional * fee_rate

        gross_pnl = qty * (exit_price - pos.entry_price) * direction
        net_pnl = gross_pnl - pos.entry_fee - exit_fee + pos.accumulated_funding

        self.cash += (gross_pnl - exit_fee)
        self.total_fees_paid += exit_fee
        self.total_gross_pnl += gross_pnl
        self.total_net_pnl += net_pnl

        trade = TradeRecord(
            trade_id=f"TR_{symbol}_{timestamp}_FULL",
            symbol=symbol,
            direction=direction,
            entry_ts=pos.entry_ts,
            exit_ts=timestamp,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            qty=qty,
            gross_pnl=gross_pnl,
            entry_fee=pos.entry_fee,
            exit_fee=exit_fee,
            funding_pnl=pos.accumulated_funding,
            net_pnl=net_pnl,
            exit_reason=reason,
            hold_hours=(timestamp - pos.entry_ts) / 3600000.0,
            trade_notional=notional,
        )
        self.closed_trades.append(trade)
        del self.positions[symbol]
        return trade

    def apply_funding(
        self,
        symbol: str,
        funding_rate: Decimal,
        timestamp: int,
        current_price: Decimal,
    ) -> Decimal:
        """
        Applies empirical 8h funding settlement to open position.
        Updates cash and position's accumulated funding cashflow.
        """
        if symbol not in self.positions:
            return Decimal("0.00")

        pos = self.positions[symbol]
        notional = pos.current_qty * current_price
        is_long = pos.direction == 1
        cf = calculate_funding_cashflow(notional, funding_rate, is_long)

        self.cash += cf
        self.total_funding_pnl += cf
        pos.accumulated_funding += cf
        return cf
