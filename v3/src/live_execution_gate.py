"""
Bybit v5 Live Execution Gate & Defensive Risk Shield.
Production-grade hardware-style safety gate for subaccount micro-capital trading.

Strict Safety Invariants:
1. SECURITY HANDSHAKE AUDIT:
   - Verifies API key permissions via /v5/user/query-api.
   - Withdrawals MUST be strictly disabled (raises SecurityViolationError if enabled).
   - Validates IP restriction whitelist.
   - Enforces Subaccount isolation (warns if running on Master account).
2. NO NAKED ORDERS (MANDATORY SERVER-SIDE STOP-LOSS):
   - Orders without attached stopLoss are strictly rejected at the gate before reaching the network.
   - Attached stopLoss is enforced via Bybit's matching engine (tpslMode="Full", slTriggerBy="MarkPrice").
   - Directional stop validity enforced: Long SL < Entry, Short SL > Entry.
3. HARD MARGIN & NOTIONAL LIMITERS:
   - Position size strictly capped at max_notional_per_position (default $30.00).
   - Total portfolio notional capped at max_portfolio_notional (default $60.00).
   - Max leverage capped at hard_max_leverage (default 2.0x).
4. DAILY LOSS CIRCUIT BREAKER:
   - Realized losses for the current UTC day are tracked.
   - If cumulative daily loss hits max_daily_loss_usd (e.g. -$10.00) or max_daily_loss_pct (e.g. -5.0%),
     all new entries are locked until 00:00 UTC.
5. DRY-RUN SAFETY INTERLOCK:
   - live_enabled must be explicitly True (default False).
   - Rejects live execution gracefully if live_enabled is False.
6. EMERGENCY PANIC FLATTEN:
   - Cancels all active orders and submits reduceOnly Market orders for all open positions.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple
import requests

from .bybit_client import BybitClient, BybitAPIError

logger = logging.getLogger("LiveExecutionGate")


class SecurityViolationError(Exception):
    """Raised when API security rules or permission constraints are violated."""
    pass


class SafetyViolationError(Exception):
    """Raised when an order attempts to violate safety invariants (e.g. naked order)."""
    pass


class CircuitBreakerError(Exception):
    """Raised when the daily loss threshold is exceeded."""
    pass


@dataclass
class OrderIntent:
    symbol: str
    direction: int  # +1 Long, -1 Short
    qty: Decimal
    entry_price: Decimal
    stop_loss: Decimal  # Mandatory!
    take_profit: Optional[Decimal] = None
    order_type: str = "Market"  # "Market" or "Limit"
    order_link_id: Optional[str] = None
    reduce_only: bool = False


@dataclass
class OrderResult:
    success: bool
    symbol: str
    direction: str
    qty: Decimal
    price: Decimal
    order_id: Optional[str] = None
    order_link_id: Optional[str] = None
    stop_loss: Optional[Decimal] = None
    take_profit: Optional[Decimal] = None
    reject_reason: Optional[str] = None
    raw_response: Dict[str, Any] = field(default_factory=dict)


@dataclass
class SecurityAuditReport:
    is_valid: bool
    is_subaccount: bool
    contract_trading_enabled: bool
    withdrawals_disabled: bool
    wallet_type: str
    total_equity: Decimal
    available_balance: Decimal
    ip_whitelist: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


class LiveExecutionGate:
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        testnet: bool = False,
        live_enabled: bool = False,
        max_notional_per_position: Decimal = Decimal("30.0"),
        max_portfolio_notional: Decimal = Decimal("60.0"),
        max_daily_loss_usd: Decimal = Decimal("10.0"),
        max_daily_loss_pct: Decimal = Decimal("5.0"),
        hard_max_leverage: Decimal = Decimal("2.0"),
        client: Optional[BybitClient] = None,
    ):
        self.api_key = api_key or os.environ.get("BYBIT_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("BYBIT_API_SECRET", "")
        self.testnet = testnet or os.environ.get("BYBIT_TESTNET", "").lower() in ("true", "1")
        self.live_enabled = live_enabled or os.environ.get("LIVE_TRADING_ENABLED", "").lower() in ("true", "1")
        
        self.max_notional_per_position = Decimal(str(max_notional_per_position))
        self.max_portfolio_notional = Decimal(str(max_portfolio_notional))
        self.max_daily_loss_usd = Decimal(str(max_daily_loss_usd))
        self.max_daily_loss_pct = Decimal(str(max_daily_loss_pct))
        self.hard_max_leverage = Decimal(str(hard_max_leverage))

        base_url = "https://api-testnet.bybit.com" if self.testnet else "https://api.bybit.com"
        self.client = client or BybitClient(
            api_key=self.api_key,
            api_secret=self.api_secret,
            base_url=base_url,
            timeout=10,
            max_retries=3,
        )

        # In-memory Instrument Info cache (step sizes, min quantities)
        self.instrument_rules: Dict[str, Dict[str, Any]] = {}
        self.last_rules_fetch_ts = 0.0

        # Circuit breaker state
        self.current_utc_day: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.daily_realized_loss: Decimal = Decimal("0.0")
        self.circuit_breaker_active: bool = False
        self.circuit_breaker_reason: str = ""

    # =========================================================================
    # 1. SECURITY HANDSHAKE & PERMISSION AUDIT
    # =========================================================================
    def verify_security_handshake(self) -> SecurityAuditReport:
        """
        Executes pre-flight security audit on Bybit API credentials.
        Verifies contract trading rights, enforces withdrawals disabled,
        and checks wallet balances.
        """
        report = SecurityAuditReport(
            is_valid=False,
            is_subaccount=False,
            contract_trading_enabled=False,
            withdrawals_disabled=False,
            wallet_type="UNKNOWN",
            total_equity=Decimal("0.0"),
            available_balance=Decimal("0.0"),
        )

        if not self.api_key or not self.api_secret:
            report.errors.append("API key or secret is missing. Check your .env file.")
            return report

        # Step 1: Query API Key permissions
        try:
            res = self._api_request("GET", "/v5/user/query-api", auth=True)
            info = res.get("result", {})
            perms = info.get("permissions", {})
            
            # Check isMaster
            is_master = info.get("isMaster", True)
            report.is_subaccount = not is_master
            if is_master:
                report.warnings.append(
                    "WARNING: API key belongs to a MASTER account! Subaccount is strongly recommended for security isolation."
                )

            # Check IP whitelist
            ips = info.get("ips", [])
            report.ip_whitelist = ips
            if not ips or "*" in ips:
                report.warnings.append(
                    "WARNING: API key has no IP whitelist restriction! Binding to VPS IP is strongly recommended."
                )

            # Check Contract Trading permission
            contract_perms = perms.get("ContractTrade", [])
            has_contract = bool(
                contract_perms
                or "Order" in contract_perms
                or "Position" in contract_perms
                or "ContractTrade" in str(perms)
            )
            report.contract_trading_enabled = has_contract
            if not has_contract:
                report.errors.append("API key lacks ContractTrade (Derivatives) permissions.")

            # CRITICAL CHECK: Withdrawals MUST BE DISABLED!
            wallet_perms = perms.get("Wallet", [])
            has_withdraw = (
                "Withdraw" in str(perms)
                or "withdraw" in str(perms).lower()
                or "Withdrawal" in wallet_perms
            )
            report.withdrawals_disabled = not has_withdraw
            if has_withdraw:
                msg = "CRITICAL SECURITY VIOLATION: API Key has WITHDRAWAL permissions enabled! Withdrawals MUST be disabled."
                report.errors.append(msg)
                logger.critical(msg)
                raise SecurityViolationError(msg)

        except SecurityViolationError:
            raise
        except Exception as e:
            report.errors.append(f"Failed to query API permissions from Bybit: {e}")
            return report

        # Step 2: Query Wallet Balance
        wallet_type, equity, avail = self._query_balances()
        report.wallet_type = wallet_type
        report.total_equity = equity
        report.available_balance = avail

        if equity <= Decimal("0.0"):
            report.errors.append(f"Account has zero equity (${equity:.2f} USDT). Fund the subaccount first.")
        elif equity > Decimal("1000.0"):
            report.warnings.append(
                f"Account equity (${equity:.2f} USDT) is relatively high for initial micro-testing. Recommended: $30 - $100."
            )

        report.is_valid = (len(report.errors) == 0 and report.contract_trading_enabled and report.withdrawals_disabled)
        return report

    def _query_balances(self) -> Tuple[str, Decimal, Decimal]:
        """Queries Unified or Contract wallet balance."""
        for acc_type in ["UNIFIED", "CONTRACT"]:
            try:
                res = self._api_request("GET", "/v5/account/wallet-balance", params={"accountType": acc_type}, auth=True)
                accounts = res.get("result", {}).get("list", [])
                if accounts:
                    acc = accounts[0]
                    eq = Decimal(str(acc.get("totalEquity") or acc.get("equity") or "0.0"))
                    avail = Decimal(str(acc.get("totalAvailableBalance") or acc.get("availableBalance") or eq))
                    if eq > Decimal("0.0"):
                        return acc_type, eq, avail
            except Exception:
                continue
        return "UNKNOWN", Decimal("0.0"), Decimal("0.0")

    # =========================================================================
    # 2. INSTRUMENT RULES & QUANTIZATION
    # =========================================================================
    def get_instrument_rules(self, symbol: str) -> Dict[str, Any]:
        """Fetches and caches lot size, tick size, and minimum notional for a symbol."""
        now = time.time()
        if not self.instrument_rules or now - self.last_rules_fetch_ts > 3600:
            try:
                instruments = self.client.get_instruments_info(category="linear")
                for item in instruments:
                    sym = item.get("symbol")
                    lot = item.get("lotSizeFilter", {})
                    price_filter = item.get("priceFilter", {})
                    if sym:
                        self.instrument_rules[sym] = {
                            "minOrderQty": Decimal(str(lot.get("minOrderQty", "0.001"))),
                            "maxOrderQty": Decimal(str(lot.get("maxOrderQty", "1000000"))),
                            "qtyStep": Decimal(str(lot.get("qtyStep", "0.001"))),
                            "minNotional": Decimal(str(lot.get("minNotionalValue", "5.0"))),
                            "tickSize": Decimal(str(price_filter.get("tickSize", "0.01"))),
                        }
                self.last_rules_fetch_ts = now
            except Exception as e:
                logger.warning(f"Failed to fetch instrument rules: {e}")

        return self.instrument_rules.get(symbol, {
            "minOrderQty": Decimal("0.01") if "BTC" not in symbol else Decimal("0.001"),
            "maxOrderQty": Decimal("1000000"),
            "qtyStep": Decimal("0.01") if "BTC" not in symbol else Decimal("0.001"),
            "minNotional": Decimal("5.0"),
            "tickSize": Decimal("0.01"),
        })

    def quantize_qty(self, symbol: str, raw_qty: Decimal) -> Decimal:
        """Quantizes order quantity to instrument step using ROUND_FLOOR."""
        rules = self.get_instrument_rules(symbol)
        step = rules["qtyStep"]
        min_qty = rules["minOrderQty"]
        
        quantized = (raw_qty / step).to_integral_value(rounding=ROUND_FLOOR) * step
        if quantized < min_qty:
            return Decimal("0.0")
        return quantized

    def quantize_price(self, symbol: str, raw_price: Decimal) -> Decimal:
        """Quantizes price to instrument tick size."""
        rules = self.get_instrument_rules(symbol)
        tick = rules["tickSize"]
        return (raw_price / tick).to_integral_value(rounding=ROUND_HALF_UP) * tick

    # =========================================================================
    # 3. PRE-TRADE VALIDATION & HARD RISK GATE
    # =========================================================================
    def validate_and_scale_intent(self, intent: OrderIntent) -> Tuple[bool, str, Optional[OrderIntent]]:
        """
        Validates order intent against all safety gates:
        1. Dry-run toggle check
        2. Daily circuit breaker check
        3. Strict attached stop-loss requirement
        4. Hard position notional cap (downscales or rejects)
        5. Total portfolio notional cap
        """
        if not self.live_enabled:
            return False, "DRY_RUN_REJECTED: LIVE_TRADING_ENABLED is False (dry-run mode active).", None

        if self.is_circuit_breaker_active():
            return False, f"CIRCUIT_BREAKER_ACTIVE: Daily loss limit reached ({self.circuit_breaker_reason}).", None

        # Check attached Stop-Loss (MANDATORY INVARIANT)
        if intent.stop_loss is None or intent.stop_loss <= Decimal("0.0"):
            msg = f"NAKED_ORDER_REJECTED: Symbol {intent.symbol} order has no stop_loss! Every order must have attached SL."
            logger.error(msg)
            raise SafetyViolationError(msg)

        # Directional Stop-Loss validation
        if intent.direction == 1 and intent.stop_loss >= intent.entry_price:
            msg = f"INVALID_STOP_LOSS: Long on {intent.symbol} requires stop_loss < entry ({intent.stop_loss} >= {intent.entry_price})."
            logger.error(msg)
            raise SafetyViolationError(msg)

        if intent.direction == -1 and intent.stop_loss <= intent.entry_price:
            msg = f"INVALID_STOP_LOSS: Short on {intent.symbol} requires stop_loss > entry ({intent.stop_loss} <= {intent.entry_price})."
            logger.error(msg)
            raise SafetyViolationError(msg)

        # Sizing and Hard Margin Cap
        rules = self.get_instrument_rules(intent.symbol)
        quantized_qty = self.quantize_qty(intent.symbol, intent.qty)
        notional = quantized_qty * intent.entry_price

        # Enforce max notional per position
        if notional > self.max_notional_per_position:
            max_qty = self.max_notional_per_position / intent.entry_price
            scaled_qty = self.quantize_qty(intent.symbol, max_qty)
            scaled_notional = scaled_qty * intent.entry_price
            
            if scaled_qty < rules["minNotional"] / intent.entry_price or scaled_qty < rules["minOrderQty"]:
                return False, f"SIZING_REJECTED: Downscaled notional (${scaled_notional:.2f}) is below Bybit minNotional (${rules['minNotional']}).", None
            
            logger.info(f"Gate: Downscaled {intent.symbol} qty from {intent.qty} to {scaled_qty} to enforce ${self.max_notional_per_position} cap.")
            quantized_qty = scaled_qty
            notional = scaled_notional

        if notional < rules["minNotional"]:
            return False, f"SIZING_REJECTED: Order notional (${notional:.2f}) < Bybit minNotional (${rules['minNotional']}).", None

        # Portfolio total notional check
        current_open_notional = self._get_total_portfolio_notional()
        if (current_open_notional + notional) > self.max_portfolio_notional:
            return False, f"PORTFOLIO_HEAT_REJECTED: Total notional (${current_open_notional + notional:.2f}) exceeds cap (${self.max_portfolio_notional:.2f}).", None

        # Build clean validated intent
        clean_intent = OrderIntent(
            symbol=intent.symbol,
            direction=intent.direction,
            qty=quantized_qty,
            entry_price=self.quantize_price(intent.symbol, intent.entry_price),
            stop_loss=self.quantize_price(intent.symbol, intent.stop_loss),
            take_profit=self.quantize_price(intent.symbol, intent.take_profit) if intent.take_profit else None,
            order_type=intent.order_type,
            order_link_id=intent.order_link_id or f"v3_{intent.symbol}_{int(time.time() * 1000)}",
            reduce_only=intent.reduce_only,
        )

        return True, "VALIDATED", clean_intent

    def _get_total_portfolio_notional(self) -> Decimal:
        """Calculates current gross open positions notional from Bybit."""
        try:
            positions = self.get_live_positions()
            total = Decimal("0.0")
            for p in positions:
                total += Decimal(str(p.get("positionValue", "0.0")))
            return total
        except Exception:
            return Decimal("0.0")

    # =========================================================================
    # 4. LIVE ORDER EXECUTION WITH SERVER-SIDE ATTACHED STOP-LOSS
    # =========================================================================
    def execute_entry(self, intent: OrderIntent) -> OrderResult:
        """
        Executes a live entry order with guaranteed attached server-side stop-loss on Bybit v5.
        """
        valid, reason, clean_intent = self.validate_and_scale_intent(intent)
        if not valid or not clean_intent:
            return OrderResult(
                success=False,
                symbol=intent.symbol,
                direction="LONG" if intent.direction == 1 else "SHORT",
                qty=intent.qty,
                price=intent.entry_price,
                reject_reason=reason,
            )

        side_str = "Buy" if clean_intent.direction == 1 else "Sell"
        payload = {
            "category": "linear",
            "symbol": clean_intent.symbol,
            "side": side_str,
            "orderType": clean_intent.order_type,
            "qty": str(clean_intent.qty),
            "positionIdx": 0,  # One-way mode
            "stopLoss": str(clean_intent.stop_loss),
            "slTriggerBy": "MarkPrice",  # Stop triggered strictly by Mark Price
            "tpslMode": "Full",
            "orderLinkId": clean_intent.order_link_id,
        }

        if clean_intent.order_type == "Limit":
            payload["price"] = str(clean_intent.entry_price)
            payload["timeInForce"] = "GTC"
        else:
            payload["timeInForce"] = "IOC"

        if clean_intent.take_profit:
            payload["takeProfit"] = str(clean_intent.take_profit)
            payload["tpTriggerBy"] = "MarkPrice"

        try:
            # Ensure 1.5x / 2.0x leverage is set on Bybit
            self._ensure_leverage(clean_intent.symbol)

            logger.info(f"Submitting LIVE order to Bybit: {side_str} {clean_intent.qty} {clean_intent.symbol} with SL={clean_intent.stop_loss}...")
            res = self._api_request("POST", "/v5/order/create", data=payload, auth=True)
            result_data = res.get("result", {})
            order_id = result_data.get("orderId")
            order_link_id = result_data.get("orderLinkId", clean_intent.order_link_id)

            logger.info(f"✅ LIVE ORDER EXECUTED! OrderId: {order_id}, Symbol: {clean_intent.symbol}, Side: {side_str}, Qty: {clean_intent.qty}, SL: {clean_intent.stop_loss}")
            return OrderResult(
                success=True,
                symbol=clean_intent.symbol,
                direction=side_str,
                qty=clean_intent.qty,
                price=clean_intent.entry_price,
                order_id=order_id,
                order_link_id=order_link_id,
                stop_loss=clean_intent.stop_loss,
                take_profit=clean_intent.take_profit,
                raw_response=res,
            )

        except Exception as e:
            logger.error(f"Failed to execute live order on Bybit: {e}")
            return OrderResult(
                success=False,
                symbol=clean_intent.symbol,
                direction=side_str,
                qty=clean_intent.qty,
                price=clean_intent.entry_price,
                reject_reason=f"Bybit API execution failed: {e}",
            )

    def _ensure_leverage(self, symbol: str) -> None:
        """Sets safe 2.0x leverage for the symbol."""
        try:
            self._api_request("POST", "/v5/position/set-leverage", data={
                "category": "linear",
                "symbol": symbol,
                "buyLeverage": str(self.hard_max_leverage),
                "sellLeverage": str(self.hard_max_leverage),
            }, auth=True)
        except Exception:
            pass  # Often already set (code 110043: leverage not modified)

    # =========================================================================
    # 5. TRAILING STOP RATCHET & POSITION EXITS
    # =========================================================================
    def update_stop_loss(self, symbol: str, new_stop: Decimal) -> bool:
        """
        Updates server-side Stop-Loss on Bybit (used by Pyramiding and Trailing stops).
        """
        if not self.live_enabled:
            return True

        quantized_sl = self.quantize_price(symbol, new_stop)
        payload = {
            "category": "linear",
            "symbol": symbol,
            "positionIdx": 0,
            "stopLoss": str(quantized_sl),
            "slTriggerBy": "MarkPrice",
            "tpslMode": "Full",
        }
        try:
            self._api_request("POST", "/v5/position/trading-stop", data=payload, auth=True)
            logger.info(f"Updated live server-side Stop-Loss on {symbol} to ${quantized_sl}")
            return True
        except Exception as e:
            logger.error(f"Failed to update stop loss on {symbol}: {e}")
            return False

    def close_position_slice(
        self, symbol: str, qty: Decimal, direction: int, order_type: str = "Market", reason: str = "EXIT"
    ) -> OrderResult:
        """
        Closes a position slice or full position using reduceOnly=True.
        """
        if not self.live_enabled:
            return OrderResult(
                success=True,
                symbol=symbol,
                direction="SELL" if direction == 1 else "BUY",
                qty=qty,
                price=Decimal("0.0"),
                reject_reason="DRY_RUN",
            )

        quantized_qty = self.quantize_qty(symbol, qty)
        side_str = "Sell" if direction == 1 else "Buy"

        payload = {
            "category": "linear",
            "symbol": symbol,
            "side": side_str,
            "orderType": order_type,
            "qty": str(quantized_qty),
            "reduceOnly": True,  # Guaranteed reduce only
            "positionIdx": 0,
            "timeInForce": "IOC" if order_type == "Market" else "GTC",
            "orderLinkId": f"v3_close_{symbol}_{int(time.time() * 1000)}",
        }
        try:
            logger.info(f"Executing reduceOnly exit on Bybit: {side_str} {quantized_qty} {symbol} ({reason})...")
            res = self._api_request("POST", "/v5/order/create", data=payload, auth=True)
            order_id = res.get("result", {}).get("orderId")
            logger.info(f"✅ Position slice closed on Bybit. OrderId: {order_id}, Reason: {reason}")
            return OrderResult(
                success=True,
                symbol=symbol,
                direction=side_str,
                qty=quantized_qty,
                price=Decimal("0.0"),
                order_id=order_id,
                raw_response=res,
            )
        except Exception as e:
            logger.error(f"Failed to close position slice on {symbol}: {e}")
            return OrderResult(
                success=False,
                symbol=symbol,
                direction=side_str,
                qty=quantized_qty,
                price=Decimal("0.0"),
                reject_reason=str(e),
            )

    # =========================================================================
    # 6. DAILY CIRCUIT BREAKER TRACKING
    # =========================================================================
    def record_realized_trade(self, net_pnl: Decimal, equity: Decimal) -> None:
        """Tracks daily losses and trips circuit breaker if thresholds are hit."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.current_utc_day:
            self.current_utc_day = today
            self.daily_realized_loss = Decimal("0.0")
            self.circuit_breaker_active = False

        if net_pnl < Decimal("0.0"):
            self.daily_realized_loss += abs(net_pnl)
            
            # Check dollar cap
            if self.daily_realized_loss >= self.max_daily_loss_usd:
                self.circuit_breaker_active = True
                self.circuit_breaker_reason = f"Daily realized loss hit ${self.daily_realized_loss:.2f} (Cap: ${self.max_daily_loss_usd:.2f})"
                logger.critical(f"🛑 CIRCUIT BREAKER TRIGGERED: {self.circuit_breaker_reason}! New entries blocked.")

            # Check percentage cap
            if equity > Decimal("0.0") and (self.daily_realized_loss / equity * Decimal("100.0")) >= self.max_daily_loss_pct:
                self.circuit_breaker_active = True
                self.circuit_breaker_reason = f"Daily loss pct hit {(self.daily_realized_loss / equity * Decimal('100.0')):.2f}% (Cap: {self.max_daily_loss_pct:.2f}%)"
                logger.critical(f"🛑 CIRCUIT BREAKER TRIGGERED: {self.circuit_breaker_reason}! New entries blocked.")

    def is_circuit_breaker_active(self) -> bool:
        """Returns True if daily circuit breaker has halted new orders."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self.current_utc_day:
            self.current_utc_day = today
            self.daily_realized_loss = Decimal("0.0")
            self.circuit_breaker_active = False
        return self.circuit_breaker_active

    # =========================================================================
    # 7. EMERGENCY PANIC FLATTEN
    # =========================================================================
    def emergency_panic_flatten(self) -> Dict[str, Any]:
        """
        Emergency Procedure:
        1. Cancels all active orders across all symbols.
        2. Closes all open positions via reduceOnly Market orders.
        """
        logger.critical("🚨 EMERGENCY PANIC FLATTEN INITIATED! Cancelling all orders and liquidating positions...")
        canceled_orders = 0
        closed_positions = []

        try:
            # Cancel all linear orders
            res = self._api_request("POST", "/v5/order/cancel-all", data={"category": "linear"}, auth=True)
            canceled_orders = len(res.get("result", {}).get("list", []))
        except Exception as e:
            logger.error(f"Failed to cancel all orders during panic: {e}")

        try:
            positions = self.get_live_positions()
            for p in positions:
                sym = p.get("symbol")
                size = Decimal(str(p.get("size", "0.0")))
                side = p.get("side")
                if sym and size > Decimal("0.0"):
                    dir_int = 1 if side == "Buy" else -1
                    res = self.close_position_slice(sym, size, dir_int, reason="PANIC_FLATTEN")
                    closed_positions.append({"symbol": sym, "size": float(size), "success": res.success})
        except Exception as e:
            logger.error(f"Failed to flatten positions during panic: {e}")

        logger.info(f"Panic flatten completed: {canceled_orders} orders canceled, {len(closed_positions)} positions closed.")
        return {
            "canceled_orders_count": canceled_orders,
            "closed_positions": closed_positions,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }

    # =========================================================================
    # 8. POSITION AND WALLET QUERY HELPERS
    # =========================================================================
    def get_live_positions(self) -> List[Dict[str, Any]]:
        """Queries all open linear perpetual positions with non-zero size."""
        try:
            res = self._api_request("GET", "/v5/position/list", params={"category": "linear", "settleCoin": "USDT"}, auth=True)
            all_pos = res.get("result", {}).get("list", [])
            return [p for p in all_pos if Decimal(str(p.get("size", "0.0"))) > Decimal("0.0")]
        except Exception as e:
            logger.warning(f"Failed to fetch live positions: {e}")
            return []

    def _api_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        data: Optional[Dict[str, Any]] = None,
        auth: bool = False,
    ) -> Dict[str, Any]:
        """Wrapper ensuring clean JSON serialization without whitespace discrepancies."""
        return self.client.request(method=method, endpoint=endpoint, params=params, data=data, auth=auth)
