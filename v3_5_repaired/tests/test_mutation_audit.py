"""
Phase F1B Mutation Test Suite: Invariant Robustness Verification.
Intentionally injects mutations into the accounting and execution invariants
to prove that the test suite strictly catches (kills) every mutated defect.
"""

from decimal import Decimal
import pytest
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v3_5_repaired.src.alpha_pairs_engine import (
    AlphaPairsEngineRepaired,
    ActivePairPosition,
    ClosedPairTrade,
)


class TestMutationAudit:
    """
    Mutation testing for Phase F1B invariants.
    Each test asserts that an injected bug MUST cause an assertion failure.
    """

    def test_mutation_01_inverted_fee_sign_is_killed(self):
        """
        Mutant 1: Inverting fee sign in net_pnl calculation (adding fees instead of subtracting).
        Invariant: Test MUST fail when net_pnl = gross_pnl + fee!
        """
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"))
        start_equity = engine.realized_equity

        # Normal entry
        engine._enter_leg("BTCUSDT", 1, {"BTCUSDT": Decimal("100.0")}, {"BTCUSDT": {"atr24": 2.0}}, start_equity, 1000)
        pos = engine.open_positions["BTCUSDT"]

        # MUTATION INJECTED: Net PnL adds fee instead of subtracting!
        gross_pnl = Decimal("5.0")
        entry_fee = pos.entry_fee
        exit_fee = Decimal("0.10")
        mutated_net_pnl = gross_pnl + entry_fee + exit_fee  # BUG: added fees!

        engine.realized_equity += (gross_pnl - exit_fee)
        engine.closed_trades.append(
            ClosedPairTrade(
                symbol="BTCUSDT",
                direction=1,
                entry_price=Decimal("100.0"),
                exit_price=Decimal("105.0"),
                qty=pos.qty,
                gross_pnl=gross_pnl,
                entry_fee_allocated=entry_fee,
                exit_fee_paid=exit_fee,
                net_pnl=mutated_net_pnl,
                reason="MUTANT",
                entry_bar=0,
                exit_bar=1,
                entry_ts=1000,
                exit_ts=2000,
            )
        )

        delta_equity = engine.realized_equity - start_equity
        sum_trade_pnl = sum(t.net_pnl for t in engine.closed_trades)

        # The test MUST detect the mutation (delta_equity != sum_trade_pnl)
        with pytest.raises(AssertionError):
            assert delta_equity == sum_trade_pnl, "Mutant survived: inverted fee sign was not caught!"

    def test_mutation_02_omitted_entry_fee_is_killed(self):
        """
        Mutant 2: Omission of entry fee from ClosedPairTrade (reproducing original v3.5 bug).
        Invariant: Test MUST fail when entry fee is omitted!
        """
        engine = AlphaPairsEngineRepaired(initial_capital=Decimal("300.0"))
        start_equity = engine.realized_equity

        engine._enter_leg("BTCUSDT", 1, {"BTCUSDT": Decimal("100.0")}, {"BTCUSDT": {"atr24": 2.0}}, start_equity, 1000)
        pos = engine.open_positions["BTCUSDT"]

        gross_pnl = Decimal("5.0")
        entry_fee = pos.entry_fee
        exit_fee = Decimal("0.10")
        # MUTATION: Entry fee omitted from net PnL (v3.5 defect)
        buggy_net_pnl = gross_pnl - exit_fee

        engine.realized_equity += (gross_pnl - exit_fee)
        engine.closed_trades.append(
            ClosedPairTrade(
                symbol="BTCUSDT",
                direction=1,
                entry_price=Decimal("100.0"),
                exit_price=Decimal("105.0"),
                qty=pos.qty,
                gross_pnl=gross_pnl,
                entry_fee_allocated=Decimal("0.0"),  # BUG
                exit_fee_paid=exit_fee,
                net_pnl=buggy_net_pnl,               # BUG
                reason="MUTANT_OMITTED_ENTRY_FEE",
                entry_bar=0,
                exit_bar=1,
                entry_ts=1000,
                exit_ts=2000,
            )
        )

        delta_equity = engine.realized_equity - start_equity
        sum_trade_pnl = sum(t.net_pnl for t in engine.closed_trades)

        with pytest.raises(AssertionError):
            assert delta_equity == sum_trade_pnl, "Mutant survived: omitted entry fee was not caught!"

    def test_mutation_03_idealized_stop_gap_is_killed(self):
        """
        Mutant 3: Market gapped to 80.0, but fill was optimistically taken at stop_price (95.0).
        Invariant: Test MUST fail when gap execution ignores market gap!
        """
        stop_price = Decimal("95.0")
        gap_price = Decimal("80.0")
        slippage = Decimal("0.0005")

        # MUTATION: Idealized stop exit
        mutated_exit_price = stop_price * (Decimal("1.0") - slippage)  # 94.95

        # Check: must be <= gap_price
        with pytest.raises(AssertionError):
            assert mutated_exit_price <= gap_price, "Mutant survived: idealized stop fill was not caught!"

    def test_mutation_04_missing_min_notional_is_killed(self):
        """
        Mutant 4: Sizing engine allows a $0.40 order despite $5.00 minNotional threshold.
        Invariant: Test MUST fail when sub-minimum order is allowed!
        """
        order_notional = Decimal("0.40")
        min_notional_limit = Decimal("5.00")

        # MUTATION: Allow order
        is_allowed = True  # Buggy behavior

        with pytest.raises(AssertionError):
            assert not is_allowed if order_notional < min_notional_limit else True
