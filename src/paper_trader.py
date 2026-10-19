"""
Paper Trading Engine & Real-Time Simulation.
Strict adherence to SPEC_RU.md Section 7:
- FSM-driven order execution
- Idempotent order_link_id
- Reduce-only protective stop management
- Real-time status reporting (capital, drawdown, position, costs, lockouts)
- Paper mode by default (live execution disabled)
"""
import logging
import time
from decimal import Decimal
from typing import Any, Dict, Optional

from src.bybit_client import BybitClient
from src.config import (
    BASE_SLIPPAGE,
    BASE_TAKER_FEE,
    StrategyParams,
)
from src.order_fsm import OrderFSM, OrderSide, OrderType, OrderState
from src.risk_manager import RiskManager
from src.strategy import Signal, StrategyV01

logger = logging.getLogger("PaperTrader")

class PaperTrader:
    """Paper execution engine mirroring Bybit V5 live API behavior."""

    def __init__(
        self,
        strategy_params: StrategyParams,
        initial_capital: Decimal = Decimal("300.0"),
        client: Optional[BybitClient] = None,
    ):
        self.strategy_params = strategy_params
        self.initial_capital = initial_capital
        self.client = client or BybitClient()
        self.fsm = OrderFSM()
        self.risk_manager = RiskManager(initial_capital=self.initial_capital)
        self.strategy = StrategyV01(params=self.strategy_params)

        self.current_position: Optional[Dict[str, Any]] = None
        self.last_signal: Optional[Signal] = None
        self.total_commissions = Decimal("0")
        self.total_funding = Decimal("0")

    def get_status(self) -> Dict[str, Any]:
        """Returns real-time state according to Section 7."""
        dd = Decimal("0")
        if self.risk_manager.peak_equity > 0:
            dd = (self.risk_manager.peak_equity - self.risk_manager.equity) / self.risk_manager.peak_equity

        return {
            "mode": "PAPER_TRADING",
            "capital": float(self.risk_manager.equity),
            "peak_equity": float(self.risk_manager.peak_equity),
            "drawdown_pct": float(dd * Decimal("100")),
            "position": self.current_position,
            "last_signal": (
                {
                    "symbol": self.last_signal.symbol,
                    "direction": "LONG" if self.last_signal.direction == 1 else "SHORT",
                    "price": self.last_signal.entry_ref_price,
                    "atr": self.last_signal.atr,
                }
                if self.last_signal
                else None
            ),
            "total_commissions": float(self.total_commissions),
            "total_funding": float(self.total_funding),
            "is_halted": self.risk_manager.is_permanently_halted,
            "halt_reason": self.risk_manager.halt_reason,
            "daily_locked": self.risk_manager.daily_lockout_until_ts > int(time.time() * 1000),
            "weekly_locked": self.risk_manager.weekly_lockout_until_ts > int(time.time() * 1000),
        }

    def execute_paper_entry(self, signal: Signal, current_market_price: Decimal) -> bool:
        """Executes paper entry order using FSM and risk controls."""
        self.last_signal = signal
        if self.current_position is not None:
            logger.info("Position already open. Skipping signal.")
            return False

        sizing = self.risk_manager.calculate_order_size(
            symbol=signal.symbol,
            direction=signal.direction,
            entry_price=current_market_price,
            atr=Decimal(str(signal.atr)),
            k_mult=signal.k_mult,
            current_funding_rate=Decimal("0.0001"),
            current_ts=int(time.time() * 1000),
        )

        if not sizing.allowed:
            logger.info(f"Entry sizing rejected: {sizing.skip_reason}")
            return False

        # Create Order in FSM
        side = OrderSide.BUY if signal.direction == 1 else OrderSide.SELL
        order = self.fsm.create_order(
            symbol=signal.symbol,
            side=side,
            order_type=OrderType.MARKET,
            qty=sizing.qty,
            reduce_only=False,
        )

        # Simulate immediate paper fill at market open with slippage
        exec_price = (
            current_market_price * (Decimal("1") + BASE_SLIPPAGE)
            if signal.direction == 1
            else current_market_price * (Decimal("1") - BASE_SLIPPAGE)
        )
        fee = sizing.qty * exec_price * BASE_TAKER_FEE
        self.total_commissions += fee

        self.fsm.transition_submitted(order.order_link_id, f"PAPER_EX_{order.order_link_id}")
        self.fsm.transition_fill(order.order_link_id, sizing.qty, exec_price, fee)

        self.current_position = {
            "symbol": signal.symbol,
            "direction": signal.direction,
            "qty": sizing.qty,
            "entry_price": exec_price,
            "stop_price": sizing.stop_price,
            "entry_time": int(time.time() * 1000),
            "order_link_id": order.order_link_id,
        }
        logger.info(f"Paper position opened: {self.current_position}")
        return True

    def execute_paper_exit(self, exit_reason: str, current_market_price: Decimal) -> bool:
        """Executes reduce-only exit in paper mode."""
        if self.current_position is None:
            return False

        pos = self.current_position
        side = OrderSide.SELL if pos["direction"] == 1 else OrderSide.BUY
        order = self.fsm.create_order(
            symbol=pos["symbol"],
            side=side,
            order_type=OrderType.MARKET,
            qty=pos["qty"],
            reduce_only=True,
        )

        exec_price = (
            current_market_price * (Decimal("1") - BASE_SLIPPAGE)
            if pos["direction"] == 1
            else current_market_price * (Decimal("1") + BASE_SLIPPAGE)
        )
        fee = pos["qty"] * exec_price * BASE_TAKER_FEE
        self.total_commissions += fee

        self.fsm.transition_submitted(order.order_link_id, f"PAPER_EX_{order.order_link_id}")
        self.fsm.transition_fill(order.order_link_id, pos["qty"], exec_price, fee)

        # Realize PnL
        if pos["direction"] == 1:
            gross = pos["qty"] * (exec_price - pos["entry_price"])
        else:
            gross = pos["qty"] * (pos["entry_price"] - exec_price)

        net_pnl = gross - fee
        self.risk_manager.equity += net_pnl
        logger.info(f"Paper position closed ({exit_reason}): NetPnL=${net_pnl:.2f}, ExecPrice=${exec_price:.2f}")
        self.current_position = None
        return True
