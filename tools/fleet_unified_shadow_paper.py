"""
Unified Lightweight Shadow Paper Engine for Bybit Quantitative Fleet.
Architecture:
- Pluggable Strategy Interface (FleetStrategyInterface).
- Supports independent Alpha Families:
    1. v3.1.4 Champion 4h Donchian Breakout
    2. v3.8 Trend Lab 1h NewDragon v4 Breakout
    3. v3.2 IRAS Range Mean-Reversion Maker (repaired causal next-bar execution)
    4. v3.11 Funding Rate Squeeze (real funding stream / threshold gate)
- Dedicated isolated run_id, SQLite database, and $300.00 capital per strategy.
- Causal Zero-Lookahead Pipeline:
    SIGNAL -> ORDER_INTENT -> SIMULATED_FILL/REJECT -> POSITION_UPDATE
- 3-Way Double-Entry Financial Reconciliation.
- Provenance tags: HISTORICAL_REPLAY vs REAL_STREAM.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import Decimal
import logging
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

import numpy as np
import pandas as pd

from tools.fleet_execution_contract import (
    ExecutionFill,
    ExecutionMode,
    FleetFinancialLedger,
    OrderIntent,
    OrderSide,
    OrderType,
    PositionSlice,
    UniversalFleetExecutionAdapter,
    STANDARD_MAKER_FEE,
    STANDARD_TAKER_FEE,
)
from v3_2.src.regime_classifier import MarketRegimeClassifier

logger = logging.getLogger("FleetUnifiedShadowPaper")


@dataclass
class ActivePaperPosition:
    symbol: str
    direction: int
    entry_price: Decimal
    stop_loss: Decimal
    take_profit: Optional[Decimal]
    slice_ref: PositionSlice
    entry_bar_idx: int


class FleetStrategyInterface(ABC):
    """Abstract pluggable strategy interface for sovereign quantitative modules."""

    def __init__(self, strategy_id: str, timeframe: str = "1h", initial_capital: Decimal = Decimal("300.0")):
        self.strategy_id = strategy_id
        self.timeframe = timeframe
        self.initial_capital = initial_capital

    @abstractmethod
    def evaluate_bar(
        self,
        symbol: str,
        current_bar: Dict[str, Any],
        historical_df: pd.DataFrame,
        open_positions: Dict[str, ActivePaperPosition],
        current_equity: Decimal,
        run_id: str,
    ) -> List[OrderIntent]:
        """Evaluates closed bar and returns generated order intents."""
        pass


class ChampionV314Strategy(FleetStrategyInterface):
    """v3.1.4 4h Donchian Breakout (Window 30, Wilder ADX >= 18.0, EMA50 > EMA200, Half-Kelly)."""

    def __init__(self, initial_capital: Decimal = Decimal("300.0")):
        super().__init__("v3.1.4_CHAMPION_4H", "4h", initial_capital)
        self.donchian_window = 30
        self.base_risk_pct = Decimal("0.010")  # 1.0% Half-Kelly
        self.atr_stop_mult = Decimal("2.5")

    def evaluate_bar(
        self,
        symbol: str,
        current_bar: Dict[str, Any],
        historical_df: pd.DataFrame,
        open_positions: Dict[str, ActivePaperPosition],
        current_equity: Decimal,
        run_id: str,
    ) -> List[OrderIntent]:
        intents = []
        if len(historical_df) < self.donchian_window + 1 or symbol in open_positions or len(open_positions) >= 4:
            return intents

        close = Decimal(str(current_bar["close"]))
        bar_start_ts = int(current_bar["timestamp"])
        bar_close_ts = bar_start_ts + (4 * 3600 * 1000)
        signal_ready_ts = bar_close_ts + 50
        data_received_ts = signal_ready_ts + 20
        order_submit_ts = data_received_ts + 10

        high_window = historical_df["high"].shift(1).iloc[-self.donchian_window:].max()
        ema_fast = float(historical_df["close"].ewm(span=50, adjust=False).mean().iloc[-1])
        ema_slow = float(historical_df["close"].ewm(span=200, adjust=False).mean().iloc[-1])
        adx = float(current_bar.get("adx", 20.0))
        atr = Decimal(str(current_bar.get("atr", float(close) * 0.02)))

        if adx < 18.0:
            return intents

        if close > Decimal(str(high_window)) and ema_fast > ema_slow:
            sl = close - (atr * self.atr_stop_mult)
            risk_dist = close - sl
            if risk_dist > 0:
                risk_usdt = current_equity * self.base_risk_pct
                qty = (risk_usdt / risk_dist).quantize(Decimal("0.001"))
                if qty > 0 and (qty * close) <= (current_equity * Decimal("3.5")):
                    intents.append(
                        OrderIntent(
                            intent_id=f"INT_314_{symbol}_{bar_close_ts}",
                            run_id=run_id,
                            module_id=self.strategy_id,
                            strategy_version="3.1.4",
                            symbol=symbol,
                            side=OrderSide.BUY,
                            order_type=OrderType.MARKET,
                            requested_qty=qty,
                            limit_price=None,
                            signal_mid_price=close,
                            signal_ts_ms=signal_ready_ts,
                            data_received_ts_ms=data_received_ts,
                            intent_reason="DONCHIAN_30_BREAKOUT_ADX18",
                            bar_start_ts_ms=bar_start_ts,
                            bar_close_ts_ms=bar_close_ts,
                            signal_ready_ts_ms=signal_ready_ts,
                            order_submit_ts_ms=order_submit_ts,
                        )
                    )
        return intents


class TrendLabV38Strategy(FleetStrategyInterface):
    """v3.8 Trend Lab 1h NewDragon v4 (Window 14, Wilder ADX >= 16.0, 1R Sizer)."""

    def __init__(self, initial_capital: Decimal = Decimal("300.0")):
        super().__init__("v3.8_TRENDLAB_1H", "1h", initial_capital)
        self.donchian_window = 14
        self.base_risk_pct = Decimal("0.008")  # 0.8% True 1R

    def evaluate_bar(
        self,
        symbol: str,
        current_bar: Dict[str, Any],
        historical_df: pd.DataFrame,
        open_positions: Dict[str, ActivePaperPosition],
        current_equity: Decimal,
        run_id: str,
    ) -> List[OrderIntent]:
        intents = []
        if len(historical_df) < self.donchian_window + 1 or symbol in open_positions or len(open_positions) >= 3:
            return intents

        close = Decimal(str(current_bar["close"]))
        bar_start_ts = int(current_bar["timestamp"])
        bar_close_ts = bar_start_ts + (1 * 3600 * 1000)
        signal_ready_ts = bar_close_ts + 50
        data_received_ts = signal_ready_ts + 20
        order_submit_ts = data_received_ts + 10

        high_window = historical_df["high"].shift(1).iloc[-self.donchian_window:].max()
        adx = float(current_bar.get("adx", 0.0))
        atr = Decimal(str(current_bar.get("atr", float(close) * 0.02)))

        if adx < 16.0:
            return intents

        if close > Decimal(str(high_window)):
            sl = close - (atr * Decimal("2.0"))
            risk_dist = close - sl
            if risk_dist > 0:
                risk_usdt = current_equity * self.base_risk_pct
                qty = (risk_usdt / risk_dist).quantize(Decimal("0.001"))
                if qty > 0 and (qty * close) <= (current_equity * Decimal("3.5")):
                    intents.append(
                        OrderIntent(
                            intent_id=f"INT_38_{symbol}_{bar_close_ts}",
                            run_id=run_id,
                            module_id=self.strategy_id,
                            strategy_version="3.8",
                            symbol=symbol,
                            side=OrderSide.BUY,
                            order_type=OrderType.MARKET,
                            requested_qty=qty,
                            limit_price=None,
                            signal_mid_price=close,
                            signal_ts_ms=signal_ready_ts,
                            data_received_ts_ms=data_received_ts,
                            intent_reason="NEWDRAGON_DONCHIAN14_ADX16",
                            bar_start_ts_ms=bar_start_ts,
                            bar_close_ts_ms=bar_close_ts,
                            signal_ready_ts_ms=signal_ready_ts,
                            order_submit_ts_ms=order_submit_ts,
                        )
                    )
        return intents


class IRASRangeStrategy(FleetStrategyInterface):
    """v3.2 IRAS Range Mean-Reversion Maker with Causal Next-Bar Evaluation."""

    def __init__(self, initial_capital: Decimal = Decimal("300.0")):
        super().__init__("v3.2_IRAS_RANGE", "1h", initial_capital)
        self.classifier = MarketRegimeClassifier(
            adx_trend_threshold=18.0,
            bbw_compression_percentile=40.0,
            min_bandwidth=0.025,
        )

    def evaluate_bar(
        self,
        symbol: str,
        current_bar: Dict[str, Any],
        historical_df: pd.DataFrame,
        open_positions: Dict[str, ActivePaperPosition],
        current_equity: Decimal,
        run_id: str,
    ) -> List[OrderIntent]:
        intents = []
        if len(historical_df) < 30 or symbol in open_positions or len(open_positions) >= 2:
            return intents

        close = Decimal(str(current_bar["close"]))
        bar_start_ts = int(current_bar["timestamp"])
        bar_close_ts = bar_start_ts + (1 * 3600 * 1000)
        signal_ready_ts = bar_close_ts + 50
        data_received_ts = signal_ready_ts + 20
        order_submit_ts = data_received_ts + 10

        adx = float(current_bar.get("adx", 20.0))
        bw = float(current_bar.get("bb_width", 0.0))
        rsi = float(current_bar.get("rsi", 50.0))
        bb_lo = Decimal(str(current_bar.get("bb_lower", float(close) * 0.98)))
        bb_mid = Decimal(str(current_bar.get("bb_mid", float(close))))
        atr = Decimal(str(current_bar.get("atr", float(close) * 0.02)))

        if adx < 18.0 and bw >= 0.025:
            if rsi < 38.0 and close >= bb_lo:
                limit_p = bb_lo
                sl = limit_p - (atr * Decimal("1.2"))
                tp = bb_mid
                risk_dist = limit_p - sl
                if risk_dist > 0 and (tp - limit_p) / limit_p >= Decimal("0.012"):
                    risk_usdt = current_equity * Decimal("0.010")
                    qty = (risk_usdt / risk_dist).quantize(Decimal("0.001"))
                    if qty > 0 and (qty * limit_p) <= (current_equity * Decimal("3.0")):
                        intents.append(
                            OrderIntent(
                                intent_id=f"INT_IRAS_{symbol}_{bar_close_ts}",
                                run_id=run_id,
                                module_id=self.strategy_id,
                                strategy_version="3.2",
                                symbol=symbol,
                                side=OrderSide.BUY,
                                order_type=OrderType.LIMIT_MAKER,
                                requested_qty=qty,
                                limit_price=limit_p,
                                signal_mid_price=close,
                                signal_ts_ms=signal_ready_ts,
                                data_received_ts_ms=data_received_ts,
                                intent_reason="IRAS_RANGE_MAKER_OVERSOLD",
                                bar_start_ts_ms=bar_start_ts,
                                bar_close_ts_ms=bar_close_ts,
                                signal_ready_ts_ms=signal_ready_ts,
                                order_submit_ts_ms=order_submit_ts,
                            )
                        )
        return intents


class FundingSqueezeStrategy(FleetStrategyInterface):
    """v3.11 Funding Squeeze with Real Rate Stream (Zero Mock)."""

    def __init__(self, initial_capital: Decimal = Decimal("300.0")):
        super().__init__("v3.11_FUNDING_SQUEEZE", "1h", initial_capital)
        self.funding_threshold = 0.0005  # |FR| >= 0.05%

    def evaluate_bar(
        self,
        symbol: str,
        current_bar: Dict[str, Any],
        historical_df: pd.DataFrame,
        open_positions: Dict[str, ActivePaperPosition],
        current_equity: Decimal,
        run_id: str,
    ) -> List[OrderIntent]:
        intents = []
        funding_rate = float(current_bar.get("funding_rate", 0.0))
        if abs(funding_rate) < self.funding_threshold:
            return intents

        close = Decimal(str(current_bar["close"]))
        bar_start_ts = int(current_bar["timestamp"])
        bar_close_ts = bar_start_ts + (1 * 3600 * 1000)
        signal_ready_ts = bar_close_ts + 50
        data_received_ts = signal_ready_ts + 20
        order_submit_ts = data_received_ts + 10

        if symbol in open_positions or len(open_positions) >= 2:
            return intents

        atr = Decimal(str(current_bar.get("atr", float(close) * 0.02)))
        risk_usdt = current_equity * Decimal("0.010")

        if funding_rate <= -self.funding_threshold:
            sl = close - (atr * Decimal("1.5"))
            tp = close + (atr * Decimal("1.2"))
            risk_dist = close - sl
            if risk_dist > 0:
                qty = (risk_usdt / risk_dist).quantize(Decimal("0.001"))
                if qty > 0 and (qty * close) <= (current_equity * Decimal("3.0")):
                    intents.append(
                        OrderIntent(
                            intent_id=f"INT_FUND_{symbol}_{bar_close_ts}",
                            run_id=run_id,
                            module_id=self.strategy_id,
                            strategy_version="3.11",
                            symbol=symbol,
                            side=OrderSide.BUY,
                            order_type=OrderType.MARKET,
                            requested_qty=qty,
                            limit_price=None,
                            signal_mid_price=close,
                            signal_ts_ms=signal_ready_ts,
                            data_received_ts_ms=data_received_ts,
                            intent_reason=f"FUNDING_SQUEEZE_RATE_{funding_rate:.5f}",
                            bar_start_ts_ms=bar_start_ts,
                            bar_close_ts_ms=bar_close_ts,
                            signal_ready_ts_ms=signal_ready_ts,
                            order_submit_ts_ms=order_submit_ts,
                        )
                    )
        return intents


class FleetUnifiedShadowPaperRunner:
    """Unified controller orchestrating multiple pluggable strategies on shared data."""

    def __init__(
        self,
        output_dir: Path,
        mode: str = "HISTORICAL_REPLAY",
    ):
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.mode = mode
        self.adapters: Dict[str, UniversalFleetExecutionAdapter] = {}
        self.strategies: Dict[str, FleetStrategyInterface] = {}
        self.active_positions: Dict[str, Dict[str, ActivePaperPosition]] = {}
        self.closed_positions_count: Dict[str, int] = {}
        self.bar_counter = 0

    def register_strategy(self, strategy: FleetStrategyInterface, run_id: str) -> None:
        """Registers a strategy with its dedicated isolated ledger and adapter."""
        db_path = self.output_dir / f"{run_id}.db"
        ledger = FleetFinancialLedger(
            db_path=str(db_path),
            initial_capital=strategy.initial_capital,
            run_id=run_id,
            module_id=strategy.strategy_id,
        )
        adapter = UniversalFleetExecutionAdapter(
            ledger=ledger,
            fee_taker=Decimal("0.0010"),
            fee_maker=Decimal("0.00036"),
            mode=ExecutionMode.HISTORICAL_REPLAY if self.mode == "HISTORICAL_REPLAY" else ExecutionMode.PAPER_V2_SHADOW,
        )
        self.strategies[strategy.strategy_id] = strategy
        self.adapters[strategy.strategy_id] = adapter
        self.active_positions[strategy.strategy_id] = {}
        self.closed_positions_count[strategy.strategy_id] = 0
        logger.info(f"Registered strategy {strategy.strategy_id} with run_id {run_id} at {db_path.name}")

    def run_replay_step(
        self,
        symbol: str,
        current_bar: Dict[str, Any],
        next_bar: Dict[str, Any],
        historical_df: pd.DataFrame,
    ) -> None:
        """Executes a single causal step across all registered strategies for a symbol."""
        self.bar_counter += 1
        next_open = Decimal(str(next_bar["open"]))
        next_high = Decimal(str(next_bar["high"]))
        next_low = Decimal(str(next_bar["low"]))
        bar_start_ts = int(next_bar["timestamp"])
        fill_ts = bar_start_ts + 100

        for strat_id, strategy in self.strategies.items():
            adapter = self.adapters[strat_id]
            positions = self.active_positions[strat_id]

            # 1. Evaluate SL/TP for open positions on incoming next_bar
            pos = positions.get(symbol)
            if pos:
                hit_sl = False
                hit_tp = False
                exit_price = pos.stop_loss
                if pos.direction == 1:
                    if next_low <= pos.stop_loss:
                        hit_sl = True
                        exit_price = pos.stop_loss
                    elif pos.take_profit and next_high >= pos.take_profit:
                        hit_tp = True
                        exit_price = pos.take_profit
                elif pos.direction == -1:
                    if next_high >= pos.stop_loss:
                        hit_sl = True
                        exit_price = pos.stop_loss
                    elif pos.take_profit and next_low <= pos.take_profit:
                        hit_tp = True
                        exit_price = pos.take_profit

                # Ambiguity resolution: Stop Loss executed first
                if hit_sl or hit_tp:
                    exit_reason = "STOP_LOSS" if hit_sl else "TAKE_PROFIT"
                    exit_side = OrderSide.SELL if pos.direction == 1 else OrderSide.BUY
                    exit_order_type = OrderType.STOP_MARKET if hit_sl else OrderType.LIMIT_MAKER
                    
                    exit_intent = OrderIntent(
                        intent_id=f"INT_EXIT_{strat_id}_{symbol}_{fill_ts}",
                        run_id=adapter.ledger.run_id,
                        module_id=strat_id,
                        strategy_version="1.0",
                        symbol=symbol,
                        side=exit_side,
                        order_type=exit_order_type,
                        requested_qty=pos.slice_ref.qty,
                        limit_price=exit_price if hit_tp else None,
                        signal_mid_price=exit_price,
                        signal_ts_ms=fill_ts - 50,
                        data_received_ts_ms=fill_ts - 20,
                        intent_reason=exit_reason,
                        bar_start_ts_ms=bar_start_ts,
                        bar_close_ts_ms=bar_start_ts + 3600000,
                        signal_ready_ts_ms=fill_ts - 50,
                        order_submit_ts_ms=fill_ts - 10,
                    )
                    adapter.submit_intent(exit_intent)
                    fill = adapter.execute_order(
                        exit_intent,
                        current_bid=exit_price,
                        current_ask=exit_price,
                        fill_ts_ms=fill_ts,
                        current_low=next_low,
                        current_high=next_high,
                        provenance="BYBIT_HISTORICAL_PARQUET_2024_2026",
                    )
                    if fill is not None:
                        adapter.close_position_slice(pos.slice_ref, fill, exit_reason)
                        del positions[symbol]
                        self.closed_positions_count[strat_id] += 1
                        continue

            # 2. Evaluate signals and submit entry intents on closed bar t
            cur_eq = adapter.ledger.get_realized_equity()
            intents = strategy.evaluate_bar(symbol, current_bar, historical_df, positions, cur_eq, adapter.ledger.run_id)
            for it in intents:
                ok, reason = adapter.submit_intent(it)
                if not ok:
                    continue

                # Execute order on next bar open (Causal next-bar execution)
                fill = adapter.execute_order(
                    intent=it,
                    current_bid=next_open if it.order_type == OrderType.MARKET else (it.limit_price or next_open),
                    current_ask=next_open if it.order_type == OrderType.MARKET else (it.limit_price or next_open),
                    fill_ts_ms=fill_ts,
                    current_low=next_low,
                    current_high=next_high,
                    provenance="BYBIT_HISTORICAL_PARQUET_2024_2026",
                )

                if fill is not None:
                    pos_id = f"POS_{uuid.uuid4().hex[:10]}"
                    dir_val = 1 if it.side == OrderSide.BUY else -1
                    slice_ref = adapter.open_position_slice(pos_id, symbol, dir_val, fill)
                    
                    # Store active position
                    positions[symbol] = ActivePaperPosition(
                        symbol=symbol,
                        direction=dir_val,
                        entry_price=fill.fill_price,
                        stop_loss=Decimal(str(it.signal_mid_price * Decimal("0.98") if dir_val == 1 else it.signal_mid_price * Decimal("1.02"))),
                        take_profit=Decimal(str(it.signal_mid_price * Decimal("1.03") if dir_val == 1 else it.signal_mid_price * Decimal("0.97"))),
                        slice_ref=slice_ref,
                        entry_bar_idx=self.bar_counter,
                    )

    def export_summary(self) -> pd.DataFrame:
        """Exports 3-way reconciliation scoreboard for all registered strategies."""
        records = []
        for strat_id, strategy in self.strategies.items():
            adapter = self.adapters[strat_id]
            ledger = adapter.ledger
            recon = ledger.get_3way_financial_reconciliation(adapter.realized_equity)
            attr = ledger.get_equity_attribution()
            current_eq = ledger.get_realized_equity()

            total_trades = recon["closed_trades_count"]

            records.append({
                "strategy_id": strat_id,
                "run_id": ledger.run_id,
                "initial_capital": float(ledger.initial_capital),
                "closed_trades": total_trades,
                "open_positions": recon["open_positions_count"],
                "gross_pnl": float(attr["sum_gross_pnl"]),
                "total_fees": float(attr["sum_fees_paid"]),
                "total_funding": float(attr["sum_funding_cashflow"]),
                "ending_realized_equity": float(current_eq),
                "recon_method_1": float(recon["method_1_slices_realized_equity"]),
                "recon_method_2": float(recon["method_2_events_realized_equity"]),
                "recon_method_3": float(recon["method_3_running_realized_equity"]),
                "recon_discrepancy": float(recon["max_discrepancy"]),
                "recon_status": recon["reconciliation_status"],
            })
        return pd.DataFrame(records)
