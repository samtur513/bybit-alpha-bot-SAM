"""
Unified Financial Event Ledger for Bybit Quantitative Systems (Phase F1B / F1C).
Enforces penny-perfect conservation of capital, double-entry cashflow attribution,
and zero-drift balance reconciliation.
"""

from dataclasses import dataclass
from decimal import Decimal
import json
import logging
import sqlite3
import time
from typing import Dict, List, Optional, Tuple, Any
import uuid

logger = logging.getLogger("FinancialLedger")


@dataclass
class LedgerEvent:
    event_id: str
    timestamp: int
    event_type: str  # 'DEPOSIT' | 'ENTRY_FEE' | 'PARTIAL_TP_SLICE' | 'FULL_CLOSE' | 'FUNDING'
    symbol: str
    trade_id: str
    direction: int
    gross_amount: Decimal
    fee_amount: Decimal
    net_cashflow: Decimal
    balance_after: Decimal
    metadata: Dict[str, Any]


class FinancialLedger:
    """
    Append-only Financial Event Ledger with SQLite WAL persistence and in-memory replay.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path
        self.events: List[LedgerEvent] = []
        if self.db_path:
            self._init_sqlite()

    def _init_sqlite(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS financial_events (
                    event_id TEXT PRIMARY KEY,
                    timestamp INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    symbol TEXT,
                    trade_id TEXT,
                    direction INTEGER,
                    gross_amount TEXT NOT NULL,
                    fee_amount TEXT NOT NULL,
                    net_cashflow TEXT NOT NULL,
                    balance_after TEXT NOT NULL,
                    metadata_json TEXT
                );
                """
            )
            conn.commit()

    def record_event(
        self,
        event_type: str,
        symbol: str,
        trade_id: str,
        direction: int,
        gross_amount: Decimal,
        fee_amount: Decimal,
        net_cashflow: Decimal,
        balance_after: Decimal,
        timestamp: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> LedgerEvent:
        ev = LedgerEvent(
            event_id=str(uuid.uuid4()),
            timestamp=timestamp or int(time.time() * 1000),
            event_type=event_type,
            symbol=symbol,
            trade_id=trade_id,
            direction=direction,
            gross_amount=Decimal(str(gross_amount)),
            fee_amount=Decimal(str(fee_amount)),
            net_cashflow=Decimal(str(net_cashflow)),
            balance_after=Decimal(str(balance_after)),
            metadata=metadata or {},
        )
        self.events.append(ev)

        if self.db_path:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO financial_events (
                        event_id, timestamp, event_type, symbol, trade_id, direction,
                        gross_amount, fee_amount, net_cashflow, balance_after, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ev.event_id,
                        ev.timestamp,
                        ev.event_type,
                        ev.symbol,
                        ev.trade_id,
                        ev.direction,
                        str(ev.gross_amount),
                        str(ev.fee_amount),
                        str(ev.net_cashflow),
                        str(ev.balance_after),
                        json.dumps(ev.metadata),
                    ),
                )
                conn.commit()

        return ev

    def record_entry_fee(
        self,
        symbol: str,
        trade_id: str,
        fee_amount: Decimal,
        equity_after: Decimal,
        timestamp: Optional[int] = None,
    ) -> LedgerEvent:
        """Deducts entry fee from realized cash."""
        return self.record_event(
            event_type="ENTRY_FEE",
            symbol=symbol,
            trade_id=trade_id,
            direction=0,
            gross_amount=Decimal("0.0"),
            fee_amount=fee_amount,
            net_cashflow=-fee_amount,
            balance_after=equity_after,
            timestamp=timestamp,
        )

    def record_slice(
        self,
        symbol: str,
        trade_id: str,
        direction: int,
        gross_pnl: Decimal,
        entry_fee_allocated: Decimal,
        exit_fee_paid: Decimal,
        equity_after: Decimal,
        timestamp: Optional[int] = None,
    ) -> LedgerEvent:
        """
        Records partial take profit slice.
        Cash change to equity is (gross_pnl - exit_fee_paid), because entry_fee was already deducted.
        Analytical net PnL is (gross_pnl - entry_fee_allocated - exit_fee_paid).
        """
        cash_delta = gross_pnl - exit_fee_paid
        net_analytical = gross_pnl - entry_fee_allocated - exit_fee_paid
        return self.record_event(
            event_type="PARTIAL_TP_SLICE",
            symbol=symbol,
            trade_id=trade_id,
            direction=direction,
            gross_amount=gross_pnl,
            fee_amount=exit_fee_paid,
            net_cashflow=cash_delta,
            balance_after=equity_after,
            timestamp=timestamp,
            metadata={
                "entry_fee_allocated": str(entry_fee_allocated),
                "analytical_net_pnl": str(net_analytical),
            },
        )

    def record_full_close(
        self,
        symbol: str,
        trade_id: str,
        direction: int,
        gross_pnl: Decimal,
        entry_fee_allocated: Decimal,
        exit_fee_paid: Decimal,
        equity_after: Decimal,
        reason: str = "STOP_LOSS",
        timestamp: Optional[int] = None,
    ) -> LedgerEvent:
        """
        Records full closure of remaining position.
        Cash change to equity is (gross_pnl - exit_fee_paid).
        """
        cash_delta = gross_pnl - exit_fee_paid
        net_analytical = gross_pnl - entry_fee_allocated - exit_fee_paid
        return self.record_event(
            event_type="FULL_CLOSE",
            symbol=symbol,
            trade_id=trade_id,
            direction=direction,
            gross_amount=gross_pnl,
            fee_amount=exit_fee_paid,
            net_cashflow=cash_delta,
            balance_after=equity_after,
            timestamp=timestamp,
            metadata={
                "entry_fee_allocated": str(entry_fee_allocated),
                "analytical_net_pnl": str(net_analytical),
                "reason": reason,
            },
        )

    def record_funding(
        self,
        symbol: str,
        cashflow_amount: Decimal,
        equity_after: Decimal,
        rate: Decimal,
        notional: Decimal,
        timestamp: Optional[int] = None,
    ) -> LedgerEvent:
        """Records 8h funding settlement cashflow."""
        return self.record_event(
            event_type="FUNDING",
            symbol=symbol,
            trade_id=f"FUNDING_{symbol}_{timestamp or int(time.time()*1000)}",
            direction=0,
            gross_amount=cashflow_amount,
            fee_amount=Decimal("0.0"),
            net_cashflow=cashflow_amount,
            balance_after=equity_after,
            timestamp=timestamp,
            metadata={
                "funding_rate": str(rate),
                "notional": str(notional),
            },
        )

    def reconcile(
        self,
        initial_capital: Decimal,
        current_equity: Decimal,
    ) -> Tuple[bool, Decimal, Decimal, Decimal]:
        """
        Performs full penny-perfect reconciliation:
        Initial Capital + Sum(Net Cashflows) == Current Realized Equity
        Returns:
            (is_balanced, expected_equity, current_equity, discrepancy)
        """
        total_cashflow = sum(ev.net_cashflow for ev in self.events)
        expected_equity = initial_capital + total_cashflow
        discrepancy = abs(current_equity - expected_equity)
        is_balanced = discrepancy < Decimal("1e-10")
        return is_balanced, expected_equity, current_equity, discrepancy
