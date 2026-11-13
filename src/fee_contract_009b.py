"""
Fee Contract Module for Bybit V5 Quantitative Fleet (FLEET-REPAIR-009B).
Step 1 Contract Implementation.

Defines the single source of truth for fee calculation across:
- Backtesting Engines (v3.0, v3.1.4, v3.2, v3.4, v3.6, v3.8, v2.1)
- Deterministic Replay Engines (L2 Orderbook, Trades)
- Live Shadow Paper Runners & Unified Adapter
- Transaction Cost Analysis (TCA) and Accounting Ledger

Rules:
1. Primary Source: Bybit V5 GET /v5/account/fee-rate.
2. Fallback Profile (Conservative Operator Profile):
   - Maker: 0.0360% (3.6 bps, Decimal("0.000360"))
   - Taker: 0.1000% (10.0 bps, Decimal("0.001000"))
3. MNT Discount (0.0324% / 0.0900%):
   - STRICTLY prohibited as default API bot rate (Bybit does not apply MNT discount to API orders).
   - Only accessible as an explicit hypothetical sensitivity scenario.
4. Accounts for funding, spread, slippage, partial fills, and per-leg executed fee.
"""

from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger("FeeContract009B")


class FeeSource(str, Enum):
    BYBIT_API_V5 = "BYBIT_API_V5"
    CONSERVATIVE_OPERATOR = "CONSERVATIVE_OPERATOR_DEFAULT"
    MNT_SENSITIVITY = "MNT_SENSITIVITY_SCENARIO"
    CUSTOM = "CUSTOM_OVERRIDE"


@dataclass(frozen=True)
class FeeProfile:
    maker_rate: Decimal
    taker_rate: Decimal
    source: FeeSource
    category: str = "linear"
    symbol: Optional[str] = None
    is_sensitivity_scenario: bool = False

    def __post_init__(self):
        if self.maker_rate < 0 or self.taker_rate < 0:
            raise ValueError(f"Fee rates cannot be negative: maker={self.maker_rate}, taker={self.taker_rate}")
        if not self.is_sensitivity_scenario and (self.maker_rate < Decimal("0.00036") or self.taker_rate < Decimal("0.0010")):
            # Guard against unauthorized discount contamination in primary runs
            if self.source not in (FeeSource.BYBIT_API_V5, FeeSource.CUSTOM):
                raise ValueError(
                    f"Sub-standard fee rates ({self.maker_rate}/{self.taker_rate}) cannot be used "
                    f"outside verified API fee-rate or explicit sensitivity scenario."
                )


# Conservative Operator Standard Profile (Non-VIP Bybit V5 API rate without MNT discount)
CONSERVATIVE_OPERATOR_PROFILE = FeeProfile(
    maker_rate=Decimal("0.000360"),   # 0.036% (3.6 bps)
    taker_rate=Decimal("0.001000"),   # 0.100% (10.0 bps)
    source=FeeSource.CONSERVATIVE_OPERATOR,
    category="linear",
    is_sensitivity_scenario=False,
)

# Hypothetical MNT Sensitivity Profile (Only for sensitivity stress testing)
MNT_SENSITIVITY_PROFILE = FeeProfile(
    maker_rate=Decimal("0.000324"),   # 0.0324% (3.24 bps)
    taker_rate=Decimal("0.000900"),   # 0.0900% (9.00 bps)
    source=FeeSource.MNT_SENSITIVITY,
    category="linear",
    is_sensitivity_scenario=True,
)


def parse_bybit_fee_response(resp: Dict[str, Any], symbol: Optional[str] = None, category: str = "linear") -> FeeProfile:
    """
    Parses Bybit V5 GET /v5/account/fee-rate response format.
    Expected schema:
    {
       "retCode": 0,
       "retMsg": "OK",
       "result": {
           "list": [
               {
                   "symbol": "BTCUSDT",
                   "takerFeeRate": "0.000600",
                   "makerFeeRate": "0.000180"
               }
           ]
       }
    }
    """
    if not isinstance(resp, dict) or resp.get("retCode") != 0:
        raise ValueError(f"Invalid Bybit fee-rate response: {resp}")

    res_list = resp.get("result", {}).get("list", [])
    if not res_list:
        raise ValueError(f"Empty fee-rate list in response: {resp}")

    item = res_list[0]
    maker_str = item.get("makerFeeRate")
    taker_str = item.get("takerFeeRate")
    sym = item.get("symbol", symbol)

    if maker_str is None or taker_str is None:
        raise ValueError(f"Missing fee rate fields in Bybit response item: {item}")

    return FeeProfile(
        maker_rate=Decimal(str(maker_str)),
        taker_rate=Decimal(str(taker_str)),
        source=FeeSource.BYBIT_API_V5,
        category=category,
        symbol=sym,
        is_sensitivity_scenario=False,
    )


def resolve_fee_profile(
    client_response: Optional[Dict[str, Any]] = None,
    symbol: Optional[str] = None,
    category: str = "linear",
    enable_mnt_sensitivity: bool = False,
) -> FeeProfile:
    """
    Resolves the appropriate FeeProfile in strict compliance with FLEET-REPAIR-009B:
    1. If client_response provided and valid -> BYBIT_API_V5.
    2. If enable_mnt_sensitivity is True -> MNT_SENSITIVITY (explicit sensitivity only).
    3. Default -> CONSERVATIVE_OPERATOR_PROFILE (0.036% maker / 0.100% taker).
    """
    if enable_mnt_sensitivity:
        return FeeProfile(
            maker_rate=MNT_SENSITIVITY_PROFILE.maker_rate,
            taker_rate=MNT_SENSITIVITY_PROFILE.taker_rate,
            source=FeeSource.MNT_SENSITIVITY,
            category=category,
            symbol=symbol,
            is_sensitivity_scenario=True,
        )

    if client_response is not None:
        try:
            return parse_bybit_fee_response(client_response, symbol=symbol, category=category)
        except Exception as e:
            logger.warning(f"Failed to parse Bybit fee rate response ({e}); falling back to Conservative Operator Profile.")

    return FeeProfile(
        maker_rate=CONSERVATIVE_OPERATOR_PROFILE.maker_rate,
        taker_rate=CONSERVATIVE_OPERATOR_PROFILE.taker_rate,
        source=FeeSource.CONSERVATIVE_OPERATOR,
        category=category,
        symbol=symbol,
        is_sensitivity_scenario=False,
    )


@dataclass
class ExecutedLeg:
    symbol: str
    side: str              # "BUY" or "SELL"
    order_type: str        # "LIMIT_MAKER" or "MARKET_TAKER"
    price: Decimal
    qty: Decimal
    fee_profile: FeeProfile
    slippage_bps: Decimal = Decimal("0")
    half_spread_bps: Decimal = Decimal("0")
    timestamp_utc: Optional[str] = None

    @property
    def is_maker(self) -> bool:
        return "MAKER" in self.order_type.upper() or "LIMIT" in self.order_type.upper() and "TAKER" not in self.order_type.upper()

    @property
    def gross_notional(self) -> Decimal:
        return self.price * self.qty

    @property
    def effective_fill_price(self) -> Decimal:
        slip_multiplier = Decimal("1") + (self.slippage_bps / Decimal("10000")) if self.side.upper() == "BUY" else Decimal("1") - (self.slippage_bps / Decimal("10000"))
        return self.price * slip_multiplier

    @property
    def effective_notional(self) -> Decimal:
        return self.effective_fill_price * self.qty

    @property
    def fee_rate(self) -> Decimal:
        return self.fee_profile.maker_rate if self.is_maker else self.fee_profile.taker_rate

    @property
    def fee_paid_usdt(self) -> Decimal:
        return self.effective_notional * self.fee_rate

    @property
    def slippage_cost_usdt(self) -> Decimal:
        return abs(self.effective_fill_price - self.price) * self.qty

    @property
    def spread_cost_usdt(self) -> Decimal:
        return self.gross_notional * (self.half_spread_bps / Decimal("10000"))

    @property
    def total_execution_drag_usdt(self) -> Decimal:
        return self.fee_paid_usdt + self.slippage_cost_usdt + self.spread_cost_usdt


def calculate_funding_cashflow(
    notional: Decimal,
    funding_rate: Decimal,
    is_long: bool,
) -> Decimal:
    """
    Computes Bybit V5 Linear Perpetual funding cashflow.
    Invariant:
    - Long pays funding if funding_rate > 0 (negative cashflow).
    - Long receives funding if funding_rate < 0 (positive cashflow).
    - Short receives funding if funding_rate > 0 (positive cashflow).
    - Short pays funding if funding_rate < 0 (negative cashflow).
    """
    if is_long:
        return -notional * funding_rate
    else:
        return notional * funding_rate
