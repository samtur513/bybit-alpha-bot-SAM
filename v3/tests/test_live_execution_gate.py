"""
Unit tests and invariant verifications for LiveExecutionGate (v3.0 Production Freeze).
Verifies:
1. Rejection of naked orders without attached stop-loss (Safety Invariant 1)
2. Directional stop-loss validity (Long SL < Entry, Short SL > Entry)
3. Dry-run safety interlock (live_enabled=False blocks live network orders)
4. Hard margin cap and quantity downscaling to stay within $30.00 notional limit
5. Daily loss circuit breaker halts new orders after hitting threshold
6. Security handshake audits permissions and strictly blocks withdrawal rights
7. Bybit v5 payload structure enforces server-side attached stop loss and MarkPrice trigger
8. Position reduction enforces reduceOnly=True
"""

from decimal import Decimal
import json
import unittest
from unittest.mock import MagicMock, patch

from v3.src.live_execution_gate import (
    LiveExecutionGate,
    OrderIntent,
    OrderResult,
    SafetyViolationError,
    SecurityViolationError,
    CircuitBreakerError,
)


class TestLiveExecutionGate(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        # Mock instrument rules cache
        self.mock_client.get_instruments_info.return_value = [
            {
                "symbol": "SOLUSDT",
                "lotSizeFilter": {
                    "minOrderQty": "0.1",
                    "maxOrderQty": "10000.0",
                    "qtyStep": "0.1",
                    "minNotionalValue": "5.0",
                },
                "priceFilter": {"tickSize": "0.01"},
            },
            {
                "symbol": "BTCUSDT",
                "lotSizeFilter": {
                    "minOrderQty": "0.001",
                    "maxOrderQty": "100.0",
                    "qtyStep": "0.001",
                    "minNotionalValue": "5.0",
                },
                "priceFilter": {"tickSize": "0.1"},
            },
        ]
        self.gate = LiveExecutionGate(
            api_key="TEST_API_KEY",
            api_secret="TEST_API_SECRET",
            live_enabled=True,
            max_notional_per_position=Decimal("30.0"),
            max_portfolio_notional=Decimal("60.0"),
            max_daily_loss_usd=Decimal("10.0"),
            max_daily_loss_pct=Decimal("5.0"),
            hard_max_leverage=Decimal("2.0"),
            client=self.mock_client,
        )

    # 1. NAKED ORDER REJECTION
    def test_rejection_of_naked_orders_without_stop_loss(self):
        intent = OrderIntent(
            symbol="SOLUSDT",
            direction=1,
            qty=Decimal("0.2"),
            entry_price=Decimal("100.0"),
            stop_loss=Decimal("0.0"),  # No SL!
        )
        with self.assertRaises(SafetyViolationError) as ctx:
            self.gate.validate_and_scale_intent(intent)
        self.assertIn("NAKED_ORDER_REJECTED", str(ctx.exception))

        intent_none = OrderIntent(
            symbol="SOLUSDT",
            direction=1,
            qty=Decimal("0.2"),
            entry_price=Decimal("100.0"),
            stop_loss=None,
        )
        with self.assertRaises(SafetyViolationError):
            self.gate.validate_and_scale_intent(intent_none)

    # 2. DIRECTIONAL STOP-LOSS VALIDATION
    def test_directional_stop_loss_validity(self):
        # Long with SL higher than entry
        long_invalid = OrderIntent(
            symbol="SOLUSDT",
            direction=1,
            qty=Decimal("0.2"),
            entry_price=Decimal("100.0"),
            stop_loss=Decimal("105.0"),  # Above entry!
        )
        with self.assertRaises(SafetyViolationError) as ctx:
            self.gate.validate_and_scale_intent(long_invalid)
        self.assertIn("INVALID_STOP_LOSS", str(ctx.exception))

        # Short with SL lower than entry
        short_invalid = OrderIntent(
            symbol="SOLUSDT",
            direction=-1,
            qty=Decimal("0.2"),
            entry_price=Decimal("100.0"),
            stop_loss=Decimal("95.0"),  # Below entry!
        )
        with self.assertRaises(SafetyViolationError) as ctx:
            self.gate.validate_and_scale_intent(short_invalid)
        self.assertIn("INVALID_STOP_LOSS", str(ctx.exception))

    # 3. DRY-RUN SAFETY INTERLOCK
    def test_dry_run_safety_interlock(self):
        self.gate.live_enabled = False
        intent = OrderIntent(
            symbol="SOLUSDT",
            direction=1,
            qty=Decimal("0.2"),
            entry_price=Decimal("100.0"),
            stop_loss=Decimal("95.0"),
        )
        valid, reason, clean = self.gate.validate_and_scale_intent(intent)
        self.assertFalse(valid)
        self.assertIn("DRY_RUN_REJECTED", reason)

        # execute_entry should not call Bybit client
        res = self.gate.execute_entry(intent)
        self.assertFalse(res.success)
        self.mock_client.request.assert_not_called()

    # 4. HARD MARGIN CAP & DOWNSCALING
    def test_hard_margin_cap_downscaling(self):
        # Intent with $100 notional (exceeds $30.0 cap)
        intent = OrderIntent(
            symbol="SOLUSDT",
            direction=1,
            qty=Decimal("1.0"),  # 1.0 * $100 = $100 notional
            entry_price=Decimal("100.0"),
            stop_loss=Decimal("95.0"),
        )
        valid, reason, clean = self.gate.validate_and_scale_intent(intent)
        self.assertTrue(valid)
        # Should be downscaled to 0.3 qty ($30.0 notional)
        self.assertEqual(clean.qty, Decimal("0.3"))
        self.assertLessEqual(clean.qty * clean.entry_price, Decimal("30.0"))

    # 5. DAILY LOSS CIRCUIT BREAKER
    def test_daily_loss_circuit_breaker(self):
        self.assertFalse(self.gate.is_circuit_breaker_active())
        equity = Decimal("300.0")

        # Loss 1: -$4.0
        self.gate.record_realized_trade(Decimal("-4.0"), equity)
        self.assertFalse(self.gate.is_circuit_breaker_active())

        # Loss 2: -$7.0 (Total: -$11.0, hits $10 cap)
        self.gate.record_realized_trade(Decimal("-7.0"), equity)
        self.assertTrue(self.gate.is_circuit_breaker_active())

        # New orders must be rejected
        intent = OrderIntent(
            symbol="SOLUSDT",
            direction=1,
            qty=Decimal("0.2"),
            entry_price=Decimal("100.0"),
            stop_loss=Decimal("95.0"),
        )
        valid, reason, clean = self.gate.validate_and_scale_intent(intent)
        self.assertFalse(valid)
        self.assertIn("CIRCUIT_BREAKER_ACTIVE", reason)

    # 6. SECURITY HANDSHAKE & WITHDRAWAL DETECTION
    def test_security_handshake_blocks_withdrawals(self):
        # API query returns WITHDRAWAL permission enabled
        self.mock_client.request.return_value = {
            "retCode": 0,
            "result": {
                "isMaster": False,
                "permissions": {
                    "ContractTrade": ["Order", "Position"],
                    "Wallet": ["AccountTransfer", "Withdrawal"],  # VIOLATION!
                },
                "ips": ["150.241.98.70"],
            },
        }

        with self.assertRaises(SecurityViolationError):
            self.gate.verify_security_handshake()

    def test_security_handshake_successful_subaccount(self):
        # API query returns valid clean permissions
        self.mock_client.request.side_effect = [
            # 1. query-api
            {
                "retCode": 0,
                "result": {
                    "isMaster": False,
                    "permissions": {
                        "ContractTrade": ["Order", "Position"],
                        "Wallet": ["AccountTransfer"],  # No withdrawals!
                    },
                    "ips": ["150.241.98.70"],
                },
            },
            # 2. wallet-balance UNIFIED
            {
                "retCode": 0,
                "result": {
                    "list": [
                        {
                            "totalEquity": "50.0",
                            "totalAvailableBalance": "50.0",
                        }
                    ]
                },
            },
        ]

        report = self.gate.verify_security_handshake()
        self.assertTrue(report.is_valid)
        self.assertTrue(report.is_subaccount)
        self.assertTrue(report.contract_trading_enabled)
        self.assertTrue(report.withdrawals_disabled)
        self.assertEqual(report.total_equity, Decimal("50.0"))

    # 7. BYBIT V5 ATTACHED STOP-LOSS ENFORCEMENT
    def test_bybit_v5_order_payload_structure(self):
        self.mock_client.request.return_value = {
            "retCode": 0,
            "result": {"orderId": "test_order_123", "orderLinkId": "v3_link_456"},
        }

        intent = OrderIntent(
            symbol="SOLUSDT",
            direction=1,
            qty=Decimal("0.2"),
            entry_price=Decimal("100.0"),
            stop_loss=Decimal("95.0"),
            take_profit=Decimal("115.0"),
            order_type="Market",
        )

        res = self.gate.execute_entry(intent)
        self.assertTrue(res.success)
        self.assertEqual(res.order_id, "test_order_123")

        # Inspect payload sent to Bybit
        calls = [c for c in self.mock_client.request.call_args_list if c.kwargs.get("endpoint") == "/v5/order/create"]
        self.assertEqual(len(calls), 1)
        sent_data = calls[0].kwargs.get("data")

        self.assertEqual(sent_data["category"], "linear")
        self.assertEqual(sent_data["symbol"], "SOLUSDT")
        self.assertEqual(sent_data["side"], "Buy")
        self.assertEqual(sent_data["stopLoss"], "95.0")
        self.assertEqual(sent_data["slTriggerBy"], "MarkPrice")
        self.assertEqual(sent_data["tpslMode"], "Full")
        self.assertEqual(sent_data["takeProfit"], "115.0")
        self.assertEqual(sent_data["tpTriggerBy"], "MarkPrice")

    # 8. POSITION REDUCE ONLY EXIT
    def test_close_position_slice_enforces_reduce_only(self):
        self.mock_client.request.return_value = {
            "retCode": 0,
            "result": {"orderId": "close_order_789"},
        }

        res = self.gate.close_position_slice(
            symbol="SOLUSDT",
            qty=Decimal("0.2"),
            direction=1,  # Long closing -> Sell
            reason="TP_HIT",
        )
        self.assertTrue(res.success)

        calls = [c for c in self.mock_client.request.call_args_list if c.kwargs.get("endpoint") == "/v5/order/create"]
        self.assertEqual(len(calls), 1)
        sent_data = calls[0].kwargs.get("data")

        self.assertEqual(sent_data["side"], "Sell")
        self.assertTrue(sent_data["reduceOnly"])
        self.assertEqual(sent_data["qty"], "0.2")


if __name__ == "__main__":
    unittest.main()
