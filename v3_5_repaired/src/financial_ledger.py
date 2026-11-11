"""
Unified Financial Event Ledger for Bybit Quantitative Systems (Phase F1B / F1C).
Enforces penny-perfect conservation of capital, double-entry cashflow attribution,
execId / settlement idempotency, SQLite WAL journaling, and crash recovery.
"""

from dataclasses import dataclass, field
from decimal import Decimal
import json
import logging
import sqlite3
import time
from typing import Dict, List, Optional, Tuple, Any, Set
import uuid

logger = logging.getLogger("FinancialLedger")


@dataclass
class LedgerEvent:
    event_id: str
    exec_id: str
    timestamp: int
    event_type: str  # 'DEPOSIT' | 'ENTRY_FEE' | 'PARTIAL_TP_SLICE' | 'FULL_CLOSE' | 'FUNDING' | 'EMERGENCY_UNWIND'
    symbol: str
    trade_id: str
    direction: int
    gross_amount: Decimal
    fee_amount: Decimal
    net_cashflow: Decimal
    balance_after: Decimal
    metadata: Dict[str, Any] = field(default_factory=dict)


class FinancialLedger:
    """
    Append-only Financial Event Ledger with SQLite WAL persistence,
    exec_id idempotency, and crash recovery.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path
        self.events: List[LedgerEvent] = []
        self.seen_exec_ids: Set[str] = set()
        self.fault_injection_pre_commit: bool = False
        self.fault_injection_during_commit: bool = False
        if self.db_path:
            self._init_sqlite()
            self._load_existing_exec_ids()

    def _init_sqlite(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS financial_events (
                    event_id TEXT PRIMARY KEY,
                    exec_id TEXT UNIQUE NOT NULL,
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
            conn.execute("CREATE INDEX IF NOT EXISTS idx_exec_id ON financial_events(exec_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_symbol ON financial_events(symbol);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON financial_events(timestamp);")
            conn.commit()

    def _load_existing_exec_ids(self) -> None:
        if not self.db_path:
            return
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT exec_id FROM financial_events")
            rows = cursor.fetchall()
            for r in rows:
                self.seen_exec_ids.add(r[0])

    def has_exec_id(self, exec_id: str) -> bool:
        """Checks if exec_id has already been processed (idempotency guard)."""
        if exec_id in self.seen_exec_ids:
            return True
        if self.db_path:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT 1 FROM financial_events WHERE exec_id = ? LIMIT 1", (exec_id,))
                return cursor.fetchone() is not None
        return False

    def record_event(
        self,
        exec_id: str,
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
    ) -> Tuple[bool, Optional[LedgerEvent]]:
        """
        Records an atomic financial transaction with strict ACID idempotency.
        
        Guarantees:
        - SQLite insert & commit happen BEFORE in-memory state is touched.
        - If SQLite fails or rolls back, in-memory events and seen_exec_ids remain 100% untouched.
        - If exec_id was already processed, returns (False, existing_event) with zero mutation.
        """
        if self.has_exec_id(exec_id):
            logger.warning(f"Duplicate exec_id '{exec_id}' detected! Idempotently rejected.")
            existing = next((e for e in self.events if e.exec_id == exec_id), None)
            return False, existing

        event_ts = timestamp or int(time.time() * 1000)
        ev = LedgerEvent(
            event_id=str(uuid.uuid4()),
            exec_id=exec_id,
            timestamp=event_ts,
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

        # 1. SQLite Transaction Execution FIRST
        if self.db_path:
            if getattr(self, "fault_injection_pre_commit", False):
                logger.error(f"Fault injection triggered: pre-commit failure on exec_id '{exec_id}'")
                return False, None

            conn = sqlite3.connect(self.db_path, timeout=10.0)
            try:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO financial_events (
                        event_id, exec_id, timestamp, event_type, symbol, trade_id, direction,
                        gross_amount, fee_amount, net_cashflow, balance_after, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        ev.event_id,
                        ev.exec_id,
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
                if getattr(self, "fault_injection_during_commit", False):
                    conn.rollback()
                    logger.error(f"Fault injection triggered: failure during commit on exec_id '{exec_id}' - explicit rollback")
                    return False, None
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
                logger.warning(f"IntegrityError: Duplicate exec_id '{exec_id}' on SQLite insert. Rolled back.")
                return False, None
            except Exception as e:
                conn.rollback()
                logger.error(f"SQLite transaction failed for exec_id '{exec_id}': {e}. Rolled back.")
                return False, None
            finally:
                conn.close()

            if getattr(self, "fault_injection_post_commit_pre_memory", False):
                logger.error(f"Fault injection triggered: crash post-commit pre-memory on exec_id '{exec_id}'")
                return False, None

        # 2. In-Memory update ONLY on successful commit
        self.events.append(ev)
        self.seen_exec_ids.add(exec_id)
        return True, ev

    def record_entry_fee(
        self,
        exec_id: str,
        symbol: str,
        trade_id: str,
        fee_amount: Decimal,
        equity_after: Decimal,
        timestamp: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[LedgerEvent]]:
        """Deducts entry fee from realized cash."""
        return self.record_event(
            exec_id=exec_id,
            event_type="ENTRY_FEE",
            symbol=symbol,
            trade_id=trade_id,
            direction=0,
            gross_amount=Decimal("0.0"),
            fee_amount=fee_amount,
            net_cashflow=-fee_amount,
            balance_after=equity_after,
            timestamp=timestamp,
            metadata=metadata or {},
        )

    def record_slice(
        self,
        exec_id: str,
        symbol: str,
        trade_id: str,
        direction: int,
        gross_pnl: Decimal,
        entry_fee_allocated: Decimal,
        exit_fee_paid: Decimal,
        equity_after: Decimal,
        timestamp: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[LedgerEvent]]:
        """
        Records partial take profit slice.
        Cash change to equity is (gross_pnl - exit_fee_paid).
        """
        cash_delta = gross_pnl - exit_fee_paid
        net_analytical = gross_pnl - entry_fee_allocated - exit_fee_paid
        meta = {
            "entry_fee_allocated": str(entry_fee_allocated),
            "analytical_net_pnl": str(net_analytical),
        }
        if metadata:
            meta.update(metadata)
        return self.record_event(
            exec_id=exec_id,
            event_type="PARTIAL_TP_SLICE",
            symbol=symbol,
            trade_id=trade_id,
            direction=direction,
            gross_amount=gross_pnl,
            fee_amount=exit_fee_paid,
            net_cashflow=cash_delta,
            balance_after=equity_after,
            timestamp=timestamp,
            metadata=meta,
        )

    def record_full_close(
        self,
        exec_id: str,
        symbol: str,
        trade_id: str,
        direction: int,
        gross_pnl: Decimal,
        entry_fee_allocated: Decimal,
        exit_fee_paid: Decimal,
        equity_after: Decimal,
        reason: str = "STOP_LOSS",
        timestamp: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[LedgerEvent]]:
        """Records full closure of remaining position."""
        cash_delta = gross_pnl - exit_fee_paid
        net_analytical = gross_pnl - entry_fee_allocated - exit_fee_paid
        meta = {
            "entry_fee_allocated": str(entry_fee_allocated),
            "analytical_net_pnl": str(net_analytical),
            "reason": reason,
        }
        if metadata:
            meta.update(metadata)
        return self.record_event(
            exec_id=exec_id,
            event_type="FULL_CLOSE",
            symbol=symbol,
            trade_id=trade_id,
            direction=direction,
            gross_amount=gross_pnl,
            fee_amount=exit_fee_paid,
            net_cashflow=cash_delta,
            balance_after=equity_after,
            timestamp=timestamp,
            metadata=meta,
        )

    def record_emergency_unwind(
        self,
        exec_id: str,
        symbol: str,
        trade_id: str,
        direction: int,
        gross_pnl: Decimal,
        entry_fee_allocated: Decimal,
        exit_fee_paid: Decimal,
        equity_after: Decimal,
        timestamp: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[LedgerEvent]]:
        """Records emergency leg unwind when counterpart leg fails."""
        cash_delta = gross_pnl - exit_fee_paid
        net_analytical = gross_pnl - entry_fee_allocated - exit_fee_paid
        meta = {
            "entry_fee_allocated": str(entry_fee_allocated),
            "analytical_net_pnl": str(net_analytical),
            "reason": "UNWIND_BROKEN_PAIR",
        }
        if metadata:
            meta.update(metadata)
        return self.record_event(
            exec_id=exec_id,
            event_type="EMERGENCY_UNWIND",
            symbol=symbol,
            trade_id=trade_id,
            direction=direction,
            gross_amount=gross_pnl,
            fee_amount=exit_fee_paid,
            net_cashflow=cash_delta,
            balance_after=equity_after,
            timestamp=timestamp,
            metadata=meta,
        )

    def record_funding(
        self,
        exec_id: str,
        symbol: str,
        cashflow_amount: Decimal,
        equity_after: Decimal,
        rate: Decimal,
        notional: Decimal,
        settlement_ts: int,
    ) -> Tuple[bool, Optional[LedgerEvent]]:
        """Records settlement funding cashflow with settlement idempotency."""
        return self.record_event(
            exec_id=exec_id,
            event_type="FUNDING",
            symbol=symbol,
            trade_id=f"FUNDING_{symbol}_{settlement_ts}",
            direction=0,
            gross_amount=cashflow_amount,
            fee_amount=Decimal("0.0"),
            net_cashflow=cashflow_amount,
            balance_after=equity_after,
            timestamp=settlement_ts,
            metadata={
                "funding_rate": str(rate),
                "notional": str(notional),
                "settlement_ts": settlement_ts,
            },
        )

    def restore_from_db(self, initial_capital: Decimal) -> Tuple[Decimal, int]:
        """
        Reconstructs state and equity from SQLite database after crash or restart.
        Returns:
            (reconstructed_equity, event_count)
        """
        if not self.db_path:
            return initial_capital, 0

        self.events.clear()
        self.seen_exec_ids.clear()
        reconstructed_equity = initial_capital

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT event_id, exec_id, timestamp, event_type, symbol, trade_id,
                       direction, gross_amount, fee_amount, net_cashflow, balance_after, metadata_json
                FROM financial_events
                ORDER BY timestamp ASC, rowid ASC
                """
            )
            rows = cursor.fetchall()
            for r in rows:
                ev = LedgerEvent(
                    event_id=r[0],
                    exec_id=r[1],
                    timestamp=r[2],
                    event_type=r[3],
                    symbol=r[4],
                    trade_id=r[5],
                    direction=r[6],
                    gross_amount=Decimal(r[7]),
                    fee_amount=Decimal(r[8]),
                    net_cashflow=Decimal(r[9]),
                    balance_after=Decimal(r[10]),
                    metadata=json.loads(r[11]) if r[11] else {},
                )
                self.events.append(ev)
                self.seen_exec_ids.add(ev.exec_id)
                reconstructed_equity += ev.net_cashflow

        return reconstructed_equity, len(self.events)

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
