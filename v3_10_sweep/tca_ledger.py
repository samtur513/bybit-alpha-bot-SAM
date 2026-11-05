import aiosqlite
import time
import uuid
from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class TCATradeRecord:
    trade_id: str
    module_id: str          # 'M3_SWEEP_8098'
    sub_strategy: str       # 'SWEEP_RECLAIM_LONG' | 'SWEEP_RECLAIM_SHORT'
    symbol: str
    side: str               # 'Buy' | 'Sell'
    opened_at_ms: int
    closed_at_ms: int
    # Noise Gate + Anti-Compression Gate Metrics
    sweep_atr_depth: float
    compression_ratio: float
    touches_count: int
    absorption_k: float
    cvd_flip_tfi: float
    # Execution Prices and Quantities
    signal_mid_price: float
    entry_fill_price: float
    exit_mid_price: float
    exit_fill_price: float
    qty: float
    # Fees and Funding
    entry_is_maker: bool
    exit_is_maker: bool
    funding_cashflow_usdt: float
    mae_pct: float
    mfe_pct: float
    exit_reason: str


class ShadowTCALedger:
    """
    Shadow Sub-Ledger for Bybit V5 Perpetual Trading.
    Performs penny-perfect 4-component TCA attribution:
      1. Pure Alpha PnL (Signal Mid -> Exit Mid)
      2. Execution Alpha / Spread Capture (Fill vs Mid at entry and exit)
      3. Funding Cashflow (Net financing yield)
      4. Exchange Fees Paid (Maker 0.0324% / Taker 0.090%)
    Also records Missed Trade Intents to measure true Opportunity Cost & Capture Efficiency.
    """

    def __init__(self, db_path: str = "tca_attribution.db", fee_maker: float = 0.000324, fee_taker: float = 0.00090):
        self.db_path = db_path
        self.fee_maker = fee_maker
        self.fee_taker = fee_taker

    async def init_db(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute("PRAGMA synchronous=NORMAL;")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS alpha_attribution_ledger (
                    trade_id TEXT PRIMARY KEY,
                    module_id TEXT,
                    sub_strategy TEXT,
                    symbol TEXT,
                    side TEXT,
                    opened_at_ms INTEGER,
                    closed_at_ms INTEGER,
                    sweep_atr_depth REAL,
                    compression_ratio REAL,
                    touches_count INTEGER,
                    absorption_k REAL,
                    cvd_flip_tfi REAL,
                    signal_mid_price REAL,
                    entry_fill_price REAL,
                    exit_mid_price REAL,
                    exit_fill_price REAL,
                    qty REAL,
                    pnl_pure_alpha_usdt REAL,
                    pnl_execution_capture_usdt REAL,
                    pnl_funding_cashflow_usdt REAL,
                    pnl_fees_paid_usdt REAL,
                    pnl_net_total_usdt REAL,
                    mae_pct REAL,
                    mfe_pct REAL,
                    exit_reason TEXT
                );
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS missed_intents_ledger (
                    intent_id TEXT PRIMARY KEY,
                    module_id TEXT,
                    symbol TEXT,
                    side TEXT,
                    created_at_ms INTEGER,
                    limit_price REAL,
                    signal_mid_price REAL,
                    mid_price_after_60s REAL,
                    qty REAL,
                    virtual_alpha_missed_usdt REAL
                );
                """
            )
            await db.commit()

    async def record_closed_trade(self, rec: TCATradeRecord) -> Dict[str, float]:
        direction = 1.0 if rec.side == "Buy" else -1.0

        # 1. Pure Alpha PnL: Price movement from signal creation to exit mid
        pnl_pure_alpha = direction * (rec.exit_mid_price - rec.signal_mid_price) * rec.qty

        # 2. Execution Alpha: Spread capture relative to mid-price
        entry_improvement = direction * (rec.signal_mid_price - rec.entry_fill_price) * rec.qty
        exit_improvement = direction * (rec.exit_fill_price - rec.exit_mid_price) * rec.qty
        pnl_exec_capture = entry_improvement + exit_improvement

        # 3. Exchange fees
        f_in = self.fee_maker if rec.entry_is_maker else self.fee_taker
        f_out = self.fee_maker if rec.exit_is_maker else self.fee_taker
        fees_usdt = -1.0 * (rec.entry_fill_price * rec.qty * f_in + rec.exit_fill_price * rec.qty * f_out)

        # 4. Net PnL (Mathematically conserved)
        pnl_net = pnl_pure_alpha + pnl_exec_capture + rec.funding_cashflow_usdt + fees_usdt

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO alpha_attribution_ledger VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    rec.trade_id, rec.module_id, rec.sub_strategy, rec.symbol, rec.side,
                    rec.opened_at_ms, rec.closed_at_ms,
                    rec.sweep_atr_depth, rec.compression_ratio, rec.touches_count,
                    rec.absorption_k, rec.cvd_flip_tfi,
                    rec.signal_mid_price, rec.entry_fill_price, rec.exit_mid_price, rec.exit_fill_price,
                    rec.qty,
                    round(pnl_pure_alpha, 5),
                    round(pnl_exec_capture, 5),
                    round(rec.funding_cashflow_usdt, 5),
                    round(fees_usdt, 5),
                    round(pnl_net, 5),
                    rec.mae_pct, rec.mfe_pct, rec.exit_reason,
                ),
            )
            await db.commit()

        return {
            "pure_alpha": pnl_pure_alpha,
            "exec_alpha": pnl_exec_capture,
            "funding": rec.funding_cashflow_usdt,
            "fees": fees_usdt,
            "net_pnl": pnl_net,
        }

    async def record_missed_intent(
        self, module_id: str, symbol: str, side: str, created_ms: int,
        limit_price: float, signal_mid: float, mid_after_60s: float, qty: float
    ) -> float:
        direction = 1.0 if side == "Buy" else -1.0
        virtual_missed = max(0.0, direction * (mid_after_60s - signal_mid) * qty)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO missed_intents_ledger VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid.uuid4()), module_id, symbol, side, created_ms,
                    limit_price, signal_mid, mid_after_60s, qty, round(virtual_missed, 5),
                ),
            )
            await db.commit()
        return virtual_missed

    async def get_summary_stats(self, module_id: Optional[str] = None) -> dict:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            where_clause = "WHERE module_id = ?" if module_id else ""
            params = (module_id,) if module_id else ()

            async with db.execute(
                f"""
                SELECT 
                    COUNT(*) as total_trades,
                    SUM(CASE WHEN pnl_net_total_usdt > 0 THEN 1 ELSE 0 END) as win_trades,
                    COALESCE(SUM(pnl_pure_alpha_usdt), 0.0) as sum_pure_alpha,
                    COALESCE(SUM(pnl_execution_capture_usdt), 0.0) as sum_exec_capture,
                    COALESCE(SUM(pnl_funding_cashflow_usdt), 0.0) as sum_funding,
                    COALESCE(SUM(pnl_fees_paid_usdt), 0.0) as sum_fees,
                    COALESCE(SUM(pnl_net_total_usdt), 0.0) as sum_net_pnl
                FROM alpha_attribution_ledger {where_clause}
                """,
                params,
            ) as cursor:
                row = await cursor.fetchone()

            async with db.execute(
                f"""
                SELECT COALESCE(SUM(virtual_alpha_missed_usdt), 0.0) as sum_missed_alpha,
                       COUNT(*) as total_missed
                FROM missed_intents_ledger {where_clause}
                """,
                params,
            ) as cursor2:
                row2 = await cursor2.fetchone()

            total_trades = row["total_trades"] if row else 0
            win_trades = row["win_trades"] if row else 0
            sum_pure_alpha = row["sum_pure_alpha"] if row else 0.0
            sum_missed = row2["sum_missed_alpha"] if row2 else 0.0
            denom = sum_pure_alpha + sum_missed
            capture_eff = (sum_pure_alpha / denom * 100.0) if denom > 0 else 100.0

            return {
                "total_trades": total_trades,
                "win_rate_pct": (win_trades / total_trades * 100.0) if total_trades > 0 else 0.0,
                "pure_alpha_usdt": round(sum_pure_alpha, 4),
                "exec_capture_usdt": round(row["sum_exec_capture"] if row else 0.0, 4),
                "funding_usdt": round(row["sum_funding"] if row else 0.0, 4),
                "fees_paid_usdt": round(row["sum_fees"] if row else 0.0, 4),
                "net_pnl_usdt": round(row["sum_net_pnl"] if row else 0.0, 4),
                "missed_alpha_usdt": round(sum_missed, 4),
                "missed_intents_count": row2["total_missed"] if row2 else 0,
                "capture_efficiency_pct": round(capture_eff, 2),
            }
