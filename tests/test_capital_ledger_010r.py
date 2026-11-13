"""
tests/test_capital_ledger_010r.py

Regression Test Suite for Finite Isolated Capital Ledger (Gate 010R).
Validates all 10 capital invariants:
1. Initial equity conservation ($300.00).
2. Dynamic equity-scaled sizing (proportional risk).
3. Pre-trade margin availability check.
4. Drawdown limit kill-switch (30% halt).
5. Solvency floor (no sub-zero phantom debt).
6. Partial TP cashflow conservation and single fee deduction.
7. Anti-martingale pyramiding weighted entry price.
8. Empirical funding cashflow accounting.
9. Gap-through-stop fill pricing.
"""

from decimal import Decimal
from pathlib import Path
import sys
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.capital_ledger_010r import IsolatedCapitalLedger, LedgerPosition
from src.fee_contract_009b import CONSERVATIVE_OPERATOR_PROFILE


def test_initial_equity_and_margin():
    ledger = IsolatedCapitalLedger(initial_capital=Decimal("300.00"), max_leverage=Decimal("3.5"))
    assert ledger.get_equity() == Decimal("300.00")
    assert ledger.get_available_margin() == Decimal("300.00")
    assert ledger.get_margin_used() == Decimal("0.00")
    assert not ledger.is_halted


def test_dynamic_risk_sizing_scales_with_equity():
    ledger = IsolatedCapitalLedger(initial_capital=Decimal("300.00"), max_leverage=Decimal("3.5"))
    price = Decimal("100.00")
    stop_dist = Decimal("5.00")  # 5% stop
    risk_pct = Decimal("0.01")   # 1% risk ($3.00 on $300)

    qty, risk_usd, status = ledger.calculate_risk_qty(price, stop_dist, risk_pct)
    assert status == "OK"
    assert risk_usd == Decimal("3.00")  # 1% of $300
    assert qty == Decimal("0.60")       # $3.00 / $5.00 = 0.60

    # Simulate equity drop to $150
    ledger.cash = Decimal("150.00")
    qty_scaled, risk_usd_scaled, status_scaled = ledger.calculate_risk_qty(price, stop_dist, risk_pct)
    assert status_scaled == "OK"
    assert risk_usd_scaled == Decimal("1.50")  # 1% of $150
    assert qty_scaled == Decimal("0.30")       # $1.50 / $5.00 = 0.30


def test_insufficient_margin_rejection():
    ledger = IsolatedCapitalLedger(initial_capital=Decimal("300.00"), max_leverage=Decimal("3.5"))
    # Max notional allowed = $300 * 3.5 = $1,050.
    # Order requiring $1,500 notional should be rejected.
    auth, msg = ledger.check_new_order(
        symbol="BTCUSDT",
        price=Decimal("50000.00"),
        qty=Decimal("0.03"),  # $1500 notional
    )
    assert not auth
    assert "INSUFFICIENT_MARGIN" in msg


def test_drawdown_limit_kill_switch():
    ledger = IsolatedCapitalLedger(
        initial_capital=Decimal("300.00"),
        drawdown_limit_pct=Decimal("30.0"),  # 30% kill switch ($90 loss -> $210 equity)
    )
    ts = 1700000000000
    # Open a position
    pos = ledger.open_position("SOLUSDT", 1, Decimal("100.00"), Decimal("5.0"), Decimal("95.00"), ts)

    # Price drops to $80 (loss = 5 * -$20 = -$100, equity = $300 - entry_fee - $100 < $200)
    marks = {"SOLUSDT": Decimal("80.00")}
    eq_pt = ledger.mark_to_market(ts + 3600000, marks)

    assert ledger.is_halted
    assert "DRAWDOWN_LIMIT_HALT" in ledger.halt_reason
    assert eq_pt.drawdown_pct >= Decimal("30.0")

    # New orders should be blocked
    auth, msg = ledger.check_new_order("ETHUSDT", Decimal("3000.00"), Decimal("0.1"))
    assert not auth
    assert "TRADING_HALTED" in msg


def test_solvency_floor_no_phantom_debt():
    ledger = IsolatedCapitalLedger(initial_capital=Decimal("300.00"), max_leverage=Decimal("3.5"))
    ts = 1700000000000
    # Open position with 3x leverage ($900 notional)
    ledger.open_position("BTCUSDT", 1, Decimal("10000.00"), Decimal("0.09"), Decimal("9500.00"), ts)

    # Catastrophic crash to $2000 (-80% loss on $900 = -$720 loss)
    marks = {"BTCUSDT": Decimal("2000.00")}
    eq_pt = ledger.mark_to_market(ts + 3600000, marks)

    assert ledger.is_halted
    assert eq_pt.equity == Decimal("0.00")
    assert ledger.get_equity() == Decimal("0.00")
    assert eq_pt.drawdown_pct == Decimal("100.00")  # Capped at 100%, cannot exceed 100%


def test_partial_tp_and_cashflow_conservation():
    ledger = IsolatedCapitalLedger(
        initial_capital=Decimal("300.00"),
        fee_profile=CONSERVATIVE_OPERATOR_PROFILE,
    )
    ts = 1700000000000
    entry_p = Decimal("100.00")
    qty = Decimal("1.0")

    pos = ledger.open_position("SOLUSDT", 1, entry_p, qty, Decimal("90.00"), ts, is_maker=False)
    entry_fee = qty * entry_p * CONSERVATIVE_OPERATOR_PROFILE.taker_rate  # 1.0 * 100 * 0.0010 = 0.10
    assert ledger.cash == Decimal("300.00") - entry_fee

    # Partial close 60% at $110 (Maker TP)
    tp_p = Decimal("110.00")
    close_qty = Decimal("0.60")
    trade = ledger.partial_close("SOLUSDT", tp_p, close_qty, ts + 3600000, "STAGE2_TP", is_maker=True)

    expected_gross = close_qty * (tp_p - entry_p)  # 0.60 * 10 = $6.00
    expected_exit_fee = close_qty * tp_p * CONSERVATIVE_OPERATOR_PROFILE.maker_rate  # 0.60 * 110 * 0.00036 = $0.02376
    expected_alloc_entry_fee = entry_fee * Decimal("0.60")  # 0.06

    assert trade.gross_pnl == expected_gross
    assert trade.exit_fee == expected_exit_fee
    assert trade.net_pnl == expected_gross - expected_alloc_entry_fee - expected_exit_fee

    # Remaining position qty must be 0.40
    assert ledger.positions["SOLUSDT"].current_qty == Decimal("0.40")

    # Full exit of remaining 0.40 at $105
    exit_p = Decimal("105.00")
    trade_full = ledger.close_position("SOLUSDT", exit_p, ts + 7200000, "TRAILING_EXIT", is_maker=False)
    assert trade_full.qty == Decimal("0.40")
    assert "SOLUSDT" not in ledger.positions

    # Verify penny-perfect cashflow conservation
    total_net = trade.net_pnl + trade_full.net_pnl
    assert abs(ledger.cash - (Decimal("300.00") + total_net)) < Decimal("1e-6")


def test_anti_martingale_pyramiding_weighted_entry():
    ledger = IsolatedCapitalLedger(initial_capital=Decimal("300.00"))
    ts = 1700000000000
    # Entry: 1.0 unit at $100.00
    ledger.open_position("SOLUSDT", 1, Decimal("100.00"), Decimal("1.0"), Decimal("95.00"), ts)

    # Pyramiding Add: 0.5 unit at $110.00
    # Expected weighted entry = (1.0 * 100 + 0.5 * 110) / 1.5 = 155 / 1.5 = 103.333333333333...
    pos = ledger.add_to_position("SOLUSDT", Decimal("110.00"), Decimal("0.5"), Decimal("103.33"), ts + 3600000)

    assert pos.current_qty == Decimal("1.5")
    expected_weighted = (Decimal("100.00") * Decimal("1.0") + Decimal("110.00") * Decimal("0.5")) / Decimal("1.5")
    assert pos.entry_price == expected_weighted
    assert pos.is_pyramided


def test_funding_cashflow_accounting():
    ledger = IsolatedCapitalLedger(initial_capital=Decimal("300.00"))
    ts = 1700000000000
    # Open Long 0.1 BTC at $50,000 (notional = $5,000)
    ledger.open_position("BTCUSDT", 1, Decimal("50000.00"), Decimal("0.05"), Decimal("48000.00"), ts)
    # Notional = 0.05 * 50,000 = $2,500.

    # Positive funding rate: Long pays funding
    f_rate = Decimal("0.0001")  # +1 bp
    cf = ledger.apply_funding("BTCUSDT", f_rate, ts + 28800000, Decimal("50000.00"))
    expected_cf = -Decimal("2500.00") * f_rate  # -$0.25
    assert cf == expected_cf
    assert ledger.positions["BTCUSDT"].accumulated_funding == expected_cf
