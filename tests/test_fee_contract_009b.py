"""
Unit Tests for Fee Contract Module (FLEET-REPAIR-009B Step 1).
"""

from decimal import Decimal
import pytest

from src.fee_contract_009b import (
    FeeProfile,
    FeeSource,
    CONSERVATIVE_OPERATOR_PROFILE,
    MNT_SENSITIVITY_PROFILE,
    parse_bybit_fee_response,
    resolve_fee_profile,
    ExecutedLeg,
    calculate_funding_cashflow,
)


def test_conservative_operator_rates():
    """Verify standard API conservative operator rates: 0.036% maker, 0.100% taker."""
    assert CONSERVATIVE_OPERATOR_PROFILE.maker_rate == Decimal("0.000360")
    assert CONSERVATIVE_OPERATOR_PROFILE.taker_rate == Decimal("0.001000")
    assert CONSERVATIVE_OPERATOR_PROFILE.source == FeeSource.CONSERVATIVE_OPERATOR
    assert not CONSERVATIVE_OPERATOR_PROFILE.is_sensitivity_scenario


def test_guard_against_unauthorized_discounts():
    """Verify that discount rates are blocked from normal production fee profiles."""
    with pytest.raises(ValueError, match="Sub-standard fee rates"):
        FeeProfile(
            maker_rate=Decimal("0.000324"),
            taker_rate=Decimal("0.000900"),
            source=FeeSource.CONSERVATIVE_OPERATOR,
            is_sensitivity_scenario=False,
        )


def test_mnt_sensitivity_scenario():
    """Verify MNT discount profile is only valid when explicitly flagged as sensitivity."""
    assert MNT_SENSITIVITY_PROFILE.maker_rate == Decimal("0.000324")
    assert MNT_SENSITIVITY_PROFILE.taker_rate == Decimal("0.000900")
    assert MNT_SENSITIVITY_PROFILE.is_sensitivity_scenario is True


def test_parse_bybit_fee_response():
    """Verify parsing real Bybit V5 GET /v5/account/fee-rate API response."""
    sample_resp = {
        "retCode": 0,
        "retMsg": "OK",
        "result": {
            "list": [
                {
                    "symbol": "BTCUSDT",
                    "takerFeeRate": "0.000550",
                    "makerFeeRate": "0.000150"
                }
            ]
        }
    }
    profile = parse_bybit_fee_response(sample_resp, symbol="BTCUSDT")
    assert profile.maker_rate == Decimal("0.000150")
    assert profile.taker_rate == Decimal("0.000550")
    assert profile.source == FeeSource.BYBIT_API_V5
    assert profile.symbol == "BTCUSDT"


def test_resolve_fee_profile_fallback():
    """Verify fallback to conservative profile when API response is missing or broken."""
    # Missing response
    p1 = resolve_fee_profile(None)
    assert p1.maker_rate == Decimal("0.000360")
    assert p1.taker_rate == Decimal("0.001000")
    assert p1.source == FeeSource.CONSERVATIVE_OPERATOR

    # Broken response
    p2 = resolve_fee_profile({"retCode": 10001, "retMsg": "Invalid API key"})
    assert p2.maker_rate == Decimal("0.000360")
    assert p2.taker_rate == Decimal("0.001000")


def test_executed_leg_accounting_taker():
    """Verify penny-perfect calculation of taker market order with slippage and spread."""
    leg = ExecutedLeg(
        symbol="BTCUSDT",
        side="BUY",
        order_type="MARKET_TAKER",
        price=Decimal("50000.00"),
        qty=Decimal("0.100"),
        fee_profile=CONSERVATIVE_OPERATOR_PROFILE,
        slippage_bps=Decimal("2.0"),      # 2 bps taker slippage
        half_spread_bps=Decimal("1.0"),   # 1 bps half spread
    )
    # Gross notional = 50000 * 0.1 = 5000
    assert leg.gross_notional == Decimal("5000.0000")

    # Effective fill price with +2 bps slip: 50000 * (1 + 0.0002) = 50010.00
    assert leg.effective_fill_price == Decimal("50010.0000")
    assert leg.effective_notional == Decimal("5001.0000")

    # Slippage cost: (50010 - 50000) * 0.1 = 1.00 USDT
    assert leg.slippage_cost_usdt == Decimal("1.0000")

    # Spread cost: 5000 * 0.0001 = 0.50 USDT
    assert leg.spread_cost_usdt == Decimal("0.5000")

    # Taker fee: 5001.00 * 0.001000 = 5.001 USDT
    assert leg.fee_paid_usdt == Decimal("5.001000")

    # Total execution drag: 5.001 + 1.00 + 0.50 = 6.501 USDT
    assert leg.total_execution_drag_usdt == Decimal("6.501000")


def test_executed_leg_accounting_maker():
    """Verify calculation of limit maker order (zero slippage, maker fee)."""
    leg = ExecutedLeg(
        symbol="ETHUSDT",
        side="SELL",
        order_type="LIMIT_MAKER",
        price=Decimal("3000.00"),
        qty=Decimal("2.0"),
        fee_profile=CONSERVATIVE_OPERATOR_PROFILE,
        slippage_bps=Decimal("0"),
        half_spread_bps=Decimal("0"),
    )
    assert leg.is_maker is True
    # Gross notional = 3000 * 2 = 6000
    assert leg.gross_notional == Decimal("6000.00")
    # Maker fee = 6000 * 0.000360 = 2.16 USDT
    assert leg.fee_paid_usdt == Decimal("2.160000")
    assert leg.total_execution_drag_usdt == Decimal("2.160000")


def test_partial_fill_accounting():
    """Verify partial execution: 60% partial close of position."""
    total_pos_qty = Decimal("1.0")
    # Leg 1: Entry 1.0 BTC @ 60,000 via Taker
    leg_entry = ExecutedLeg(
        symbol="BTCUSDT",
        side="BUY",
        order_type="MARKET_TAKER",
        price=Decimal("60000.00"),
        qty=total_pos_qty,
        fee_profile=CONSERVATIVE_OPERATOR_PROFILE,
    )
    assert leg_entry.fee_paid_usdt == Decimal("60.000000")  # 60,000 * 0.001

    # Leg 2: Partial Close 0.60 BTC @ 63,000 via Maker Limit
    leg_partial = ExecutedLeg(
        symbol="BTCUSDT",
        side="SELL",
        order_type="LIMIT_MAKER",
        price=Decimal("63000.00"),
        qty=Decimal("0.60"),
        fee_profile=CONSERVATIVE_OPERATOR_PROFILE,
    )
    # Notional = 63,000 * 0.60 = 37,800
    # Maker fee = 37,800 * 0.000360 = 13.608 USDT
    assert leg_partial.fee_paid_usdt == Decimal("13.608000")

    # Leg 3: Final Close 0.40 BTC @ 64,000 via Taker Stop
    leg_final = ExecutedLeg(
        symbol="BTCUSDT",
        side="SELL",
        order_type="MARKET_TAKER",
        price=Decimal("64000.00"),
        qty=Decimal("0.40"),
        fee_profile=CONSERVATIVE_OPERATOR_PROFILE,
    )
    # Notional = 64,000 * 0.40 = 25,600
    # Taker fee = 25,600 * 0.001000 = 25.60 USDT
    assert leg_final.fee_paid_usdt == Decimal("25.600000")

    total_fees = leg_entry.fee_paid_usdt + leg_partial.fee_paid_usdt + leg_final.fee_paid_usdt
    assert total_fees == Decimal("99.208000")


def test_funding_cashflow():
    """Verify funding cashflow semantics on Bybit V5 Linear Perpetuals."""
    notional = Decimal("10000.00")
    pos_funding = Decimal("0.000300")   # +3 bps (Long pays, Short receives)
    neg_funding = Decimal("-0.000200")  # -2 bps (Long receives, Short pays)

    # Long in positive funding: pays 3 USDT
    cf_long_pos = calculate_funding_cashflow(notional, pos_funding, is_long=True)
    assert cf_long_pos == Decimal("-3.000000")

    # Short in positive funding: receives 3 USDT
    cf_short_pos = calculate_funding_cashflow(notional, pos_funding, is_long=False)
    assert cf_short_pos == Decimal("3.000000")

    # Long in negative funding: receives 2 USDT
    cf_long_neg = calculate_funding_cashflow(notional, neg_funding, is_long=True)
    assert cf_long_neg == Decimal("2.000000")

    # Short in negative funding: pays 2 USDT
    cf_short_neg = calculate_funding_cashflow(notional, neg_funding, is_long=False)
    assert cf_short_neg == Decimal("-2.000000")
