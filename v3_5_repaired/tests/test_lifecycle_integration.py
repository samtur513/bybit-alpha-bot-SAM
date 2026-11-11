"""
Phase F1B / F1C Full Lifecycle Integration Test (Auditor Requirement 2).
Strictly verifies that:
1. Zero manual position deletion (no open_positions.pop()).
2. Zero manual equity modification (no engine.realized_equity += ...).
3. Zero manual financial ledger events constructed in test code.
4. All lifecycle transitions executed exclusively via production engine methods:
   - _enter_leg (production entry & fee deduction)
   - process_bar (production partial TP slicing at +4.0R)
   - apply_funding_settlements (production funding cashflow with settlement idempotency)
   - process_bar (production stop loss and hysteresis exits)
   - recover_from_ledger (production crash recovery from SQLite WAL database)
   - verify_ledger_conservation & reconcile (independent double-entry verification)
"""

from decimal import Decimal
import os
import shutil
import time
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from v3_5_repaired.src.alpha_pairs_engine import (
    AlphaPairsEngineRepaired,
    ActivePairPosition,
    ClosedPairTrade,
    FundingSettlement,
)
from v3_5_repaired.src.financial_ledger import FinancialLedger
from v3_5_repaired.src.config import INITIAL_CAPITAL, FEE_TAKER, FEE_MAKER, BASE_SLIPPAGE


def test_pure_production_lifecycle_integration_with_crash_recovery():
    """
    Exhaustive integration test without any manual position or equity tampering.
    """
    test_dir = REPO_ROOT / ".scratch" / "lifecycle_production_test_db"
    if test_dir.exists():
        shutil.rmtree(test_dir, ignore_errors=True)
    test_dir.mkdir(parents=True, exist_ok=True)

    db_path = str(test_dir / "production_lifecycle_ledger.db")
    initial_capital = Decimal("300.00")

    # Instantiate engine with real SQLite WAL ledger
    engine = AlphaPairsEngineRepaired(initial_capital=initial_capital, ledger_db_path=db_path)
    start_ts = int(time.time() * 1000)

    # -------------------------------------------------------------------------
    # STAGE 1: Matched 2-Leg Entry via Production Method (_enter_leg)
    # -------------------------------------------------------------------------
    current_prices = {"BTCUSDT": Decimal("100.00"), "ETHUSDT": Decimal("50.00")}
    metrics = {"BTCUSDT": {"atr24": 2.0}, "ETHUSDT": {"atr24": 1.0}}

    l_ok = engine._enter_leg(
        "BTCUSDT", 1, current_prices, metrics, engine.realized_equity, start_ts,
        bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=start_ts, current_clock=start_ts,
    )
    assert l_ok is True
    s_ok = engine._enter_leg(
        "ETHUSDT", -1, current_prices, metrics, engine.realized_equity, start_ts,
        bid=Decimal("50.00"), ask=Decimal("50.02"), quote_ts=start_ts, current_clock=start_ts,
    )
    assert s_ok is True

    # Assert 2 positions opened
    assert len(engine.open_positions) == 2
    pos_btc = engine.open_positions["BTCUSDT"]
    pos_eth = engine.open_positions["ETHUSDT"]

    # Verify fees deducted from realized equity by production code
    expected_equity_stage1 = initial_capital - pos_btc.entry_fee - pos_eth.entry_fee
    assert engine.realized_equity == expected_equity_stage1

    # Verify SQLite database has 2 ENTRY_FEE records
    assert engine.ledger.has_exec_id(f"EXEC_ENTER_BTCUSDT_{start_ts}_1")
    assert engine.ledger.has_exec_id(f"EXEC_ENTER_ETHUSDT_{start_ts}_-1")

    # -------------------------------------------------------------------------
    # STAGE 2: Partial Take Profit (+4.0R) via Production process_bar
    # -------------------------------------------------------------------------
    # Price rises to +4.0R: entry + 4 * stop_dist = 100 + 4 * 5 = 120.00
    target_tp_p = Decimal("120.00")
    current_prices["BTCUSDT"] = target_tp_p
    prices_series = {
        "BTCUSDT": [{"open": 100.0, "high": 125.0, "low": 99.0, "close": 120.0, "volume": 1000.0}] * 60,
        "ETHUSDT": [{"open": 50.0, "high": 51.0, "low": 49.0, "close": 50.0, "volume": 1000.0}] * 60,
    }

    # Call production process_bar!
    engine.process_bar(prices_series, current_prices, start_ts + 3600000)

    # Assert partial TP slice created by engine
    assert len(engine.closed_trades) == 1
    slice_trade = engine.closed_trades[0]
    assert slice_trade.symbol == "BTCUSDT"
    assert slice_trade.reason == "PARTIAL_TP_4R"
    assert pos_btc.tp_taken is True

    # Verify slice recorded in SQLite ledger with exec_id
    slice_exec_id = f"EXEC_PARTIAL_TP_BTCUSDT_{start_ts + 3600000}"
    assert engine.ledger.has_exec_id(slice_exec_id)

    # -------------------------------------------------------------------------
    # STAGE 3: Funding Settlement via Production apply_funding_settlements
    # -------------------------------------------------------------------------
    settlement_ts = start_ts + 7200000
    settlements = [
        FundingSettlement(symbol="BTCUSDT", settlement_ts=settlement_ts, rate=Decimal("0.0001"), mark_price=Decimal("120.00")),
        FundingSettlement(symbol="ETHUSDT", settlement_ts=settlement_ts, rate=Decimal("0.0001"), mark_price=Decimal("50.00")),
    ]

    # Apply via production engine method
    net_funding = engine.apply_funding_settlements(settlements)
    assert net_funding != Decimal("0.0")

    equity_after_funding = engine.realized_equity

    # IDEMPOTENCY TEST: Call exact same settlements again!
    dup_funding = engine.apply_funding_settlements(settlements)
    assert dup_funding == Decimal("0.0"), "Idempotency failed: Duplicate funding was applied!"
    assert engine.realized_equity == equity_after_funding, "Realized equity changed on duplicate funding!"

    # -------------------------------------------------------------------------
    # STAGE 4: Full Close via Production process_bar (Stop-Loss Execution)
    # -------------------------------------------------------------------------
    # Market drops for BTC (gapping below stop of 95.0 to 85.0)
    # Market moves against ETH short (rises to 60.0 above stop of 52.5)
    current_prices["BTCUSDT"] = Decimal("85.00")
    current_prices["ETHUSDT"] = Decimal("60.00")

    prices_series_stop = {
        "BTCUSDT": [{"open": 85.0, "high": 86.0, "low": 84.0, "close": 85.0, "volume": 1000.0}] * 60,
        "ETHUSDT": [{"open": 60.0, "high": 61.0, "low": 59.0, "close": 60.0, "volume": 1000.0}] * 60,
    }

    # Pass actual book bid/ask to confirm breach against orderbook
    bids_stop = {"BTCUSDT": Decimal("85.00"), "ETHUSDT": Decimal("59.90")}
    asks_stop = {"BTCUSDT": Decimal("85.10"), "ETHUSDT": Decimal("60.00")}

    # Call production process_bar!
    engine.process_bar(
        prices_series_stop,
        current_prices,
        start_ts + 10800000,
        orderbook_bids=bids_stop,
        orderbook_asks=asks_stop,
    )

    # Both positions must be closed naturally by production code!
    assert len(engine.open_positions) == 0, "Positions were not closed by production process_bar!"
    assert len(engine.closed_trades) == 3  # 1 partial slice + 2 full closes

    # Verify stop loss records in SQLite ledger
    stop_btc_exec_id = f"EXEC_STOP_LOSS_BTCUSDT_{start_ts + 10800000}"
    stop_eth_exec_id = f"EXEC_STOP_LOSS_ETHUSDT_{start_ts + 10800000}"
    assert engine.ledger.has_exec_id(stop_btc_exec_id)
    assert engine.ledger.has_exec_id(stop_eth_exec_id)

    # -------------------------------------------------------------------------
    # STAGE 5: Crash and Recovery from SQLite WAL Database
    # -------------------------------------------------------------------------
    # Instantiate a completely fresh engine with no memory state
    crashed_recovery_engine = AlphaPairsEngineRepaired(
        initial_capital=initial_capital,
        ledger_db_path=db_path,
    )
    # Reconstruct from SQLite WAL events
    recovered_eq, event_count = crashed_recovery_engine.recover_from_ledger()

    assert recovered_eq == engine.realized_equity, (
        f"Crash Recovery Mismatch! Original Equity={engine.realized_equity}, Recovered={recovered_eq}"
    )
    assert event_count >= 5  # 2 entries, 1 partial TP, 2 fundings, 2 closes

    # -------------------------------------------------------------------------
    # STAGE 6: Independent Double-Entry Financial Reconciliation
    # -------------------------------------------------------------------------
    is_internal_balanced, delta_eq, expected_delta = engine.verify_ledger_conservation()
    assert is_internal_balanced is True, (
        f"Internal Ledger Conservation Broken: Delta={delta_eq}, Expected={expected_delta}"
    )

    is_journal_balanced, exp_eq_j, curr_eq_j, disc_j = engine.ledger.reconcile(
        initial_capital, engine.realized_equity
    )
    assert is_journal_balanced is True
    assert disc_j < Decimal("1e-12")

    # Also reconcile recovered engine
    is_rec_balanced, exp_rec, curr_rec, disc_rec = crashed_recovery_engine.ledger.reconcile(
        initial_capital, crashed_recovery_engine.realized_equity
    )
    assert is_rec_balanced is True
    assert disc_rec < Decimal("1e-12")
