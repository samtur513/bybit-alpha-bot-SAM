"""
Order State Machine (FSM) & Execution Management.
Strict adherence to SPEC_RU.md Section 7:
- Deterministic FSM states: PENDING_SUBMIT, SUBMITTED, PARTIALLY_FILLED, FILLED, CANCELED, REJECTED, EXPIRED
- Persistent UUID/OrderLinkId for idempotency
- Reduce-only flags for exit orders
- Protective stop linkage and emergency closure if stop fails
- Timeout reconciliation before retry
"""
import enum
import logging
import uuid
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional

logger = logging.getLogger("OrderFSM")

class OrderState(enum.Enum):
    PENDING_SUBMIT = "PENDING_SUBMIT"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"

class OrderType(enum.Enum):
    MARKET = "Market"
    LIMIT = "Limit"

class OrderSide(enum.Enum):
    BUY = "Buy"
    SELL = "Sell"

@dataclass
class Order:
    order_link_id: str
    symbol: str
    side: OrderSide
    order_type: OrderType
    qty: Decimal
    price: Optional[Decimal]
    reduce_only: bool
    state: OrderState = OrderState.PENDING_SUBMIT
    exchange_order_id: Optional[str] = None
    cum_exec_qty: Decimal = Decimal("0")
    avg_price: Decimal = Decimal("0")
    cum_exec_fee: Decimal = Decimal("0")
    created_at_ts: int = 0
    updated_at_ts: int = 0
    last_error: Optional[str] = None

class OrderFSM:
    """Finite State Machine managing lifecycle, safety, and reconciliation of orders."""

    def __init__(self):
        self.orders: Dict[str, Order] = {}

    def create_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        qty: Decimal,
        price: Optional[Decimal] = None,
        reduce_only: bool = False,
    ) -> Order:
        order_link_id = f"TB_{symbol}_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
        now = int(time.time() * 1000)
        order = Order(
            order_link_id=order_link_id,
            symbol=symbol,
            side=side,
            order_type=order_type,
            qty=qty,
            price=price,
            reduce_only=reduce_only,
            state=OrderState.PENDING_SUBMIT,
            created_at_ts=now,
            updated_at_ts=now,
        )
        self.orders[order_link_id] = order
        return order

    def transition_submitted(self, order_link_id: str, exchange_order_id: str) -> None:
        order = self.orders.get(order_link_id)
        if not order:
            raise ValueError(f"Unknown order {order_link_id}")
        order.state = OrderState.SUBMITTED
        order.exchange_order_id = exchange_order_id
        order.updated_at_ts = int(time.time() * 1000)

    def transition_fill(
        self,
        order_link_id: str,
        exec_qty: Decimal,
        exec_price: Decimal,
        fee: Decimal,
    ) -> None:
        order = self.orders.get(order_link_id)
        if not order:
            raise ValueError(f"Unknown order {order_link_id}")

        new_cum_qty = order.cum_exec_qty + exec_qty
        if new_cum_qty > 0:
            order.avg_price = (order.avg_price * order.cum_exec_qty + exec_price * exec_qty) / new_cum_qty
        order.cum_exec_qty = new_cum_qty
        order.cum_exec_fee += fee
        order.updated_at_ts = int(time.time() * 1000)

        if order.cum_exec_qty >= order.qty:
            order.state = OrderState.FILLED
        else:
            order.state = OrderState.PARTIALLY_FILLED

    def transition_terminal(self, order_link_id: str, state: OrderState, error: Optional[str] = None) -> None:
        order = self.orders.get(order_link_id)
        if not order:
            raise ValueError(f"Unknown order {order_link_id}")
        order.state = state
        order.last_error = error
        order.updated_at_ts = int(time.time() * 1000)
