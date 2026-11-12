"""
FastTrack 003 Native Strategy Runner & Unidirectional Execution Adapter Harness
Module: tools/native_fleet_runner_fasttrack.py

Implements strict unidirectional causal execution:
SIGNAL -> ORDER_INTENT -> SIMULATED_FILL/REJECT -> POSITION_UPDATE

Key Invariants:
1. Capital & Financial Ledger Isolation:
   - Dedicated run_id, isolated SQLite database, separate $300.0 initial capital.
   - Zero shared realized or MTM equity between independent strategies.
2. Strict Time Causality:
   - bar_start_ts < bar_close_ts <= signal_ready_ts <= data_received_ts <= order_submit_ts <= fill_ts.
   - Point-in-Time execution: Bar t closes before signal evaluates; entry fills at Open_{t+1}.
   - Conservative Ambiguity Resolution: If Stop and TP touch in the same bar, execute Stop first.
3. True Alpha Logic Restoration:
   - v3.1.4: 4h Donchian 30, Wilder ADX(14) >= 18.0, EMA50 > EMA200, BTC Delta, 1.0% Half-Kelly,
             ATR stop 2.5x, Trailing stop 2.5x, enable_cascade_tp = False.
   - v3.8: 1h Donchian 14, Wilder ADX(14) >= 16.0 (calculated with real Wilder smoothing, NO mock!),
           True 1R Sizer (0.8% risk, 3.5x leverage cap, max 3 positions),
           4-Stage Adaptive Exit FSM (Stage 0: Scratch, Stage 1: Ratchet, Stage 2: Maker TP 60%, Stage 3: Chandelier).
4. Provenance & Execution Realism:
   - Tagged HISTORICAL_REPLAY.
   - VIP0 conservative fees: Taker = 0.100%, Maker = 0.036%, half-spread = 0.02%, slippage = 0.02%.
   - Full 3-Way Financial Reconciliation: Slices Net PnL vs Events Journal vs Running Equity.
"""

import os
import sys
import math
import logging
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.fleet_execution_contract import (
    FleetFinancialLedger,
    UniversalFleetExecutionAdapter,
    OrderIntent,
    ExecutionFill,
    FundingEvent,
    PositionSlice,
    ExecutionMode,
    OrderSide,
    OrderType,
    STANDARD_TAKER_FEE,
    STANDARD_MAKER_FEE,
)

from v3.src.indicators import calculate_adx
from v3.src.multi_scanner import MultiCoinScanner, CoinCandidate
from v3_12_crossbasis.crossbasis_8096 import CrossBasisEngine8096, SpotState

logger = logging.getLogger("NativeFleetRunnerFastTrack")

SYMBOLS_18 = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "DOGEUSDT", "AVAXUSDT",
    "NEARUSDT", "LINKUSDT", "APTUSDT", "ADAUSDT", "XRPUSDT", "BNBUSDT",
    "TIAUSDT", "INJUSDT", "ARBUSDT", "OPUSDT", "1000PEPEUSDT", "RENDERUSDT"
]


def get_standard_instrument_rules() -> Dict[str, Dict[str, Decimal]]:
    """Official Bybit V5 Linear Perpetual trading specifications for 18 universe coins."""
    rules = {}
    for sym in SYMBOLS_18:
        if sym in ("BTCUSDT",):
            rules[sym] = {"minOrderQty": Decimal("0.001"), "qtyStep": Decimal("0.001"), "minNotional": Decimal("5.0"), "maxOrderQty": Decimal("100.0")}
        elif sym in ("ETHUSDT",):
            rules[sym] = {"minOrderQty": Decimal("0.01"), "qtyStep": Decimal("0.01"), "minNotional": Decimal("5.0"), "maxOrderQty": Decimal("500.0")}
        elif sym in ("SOLUSDT", "AVAXUSDT", "BNBUSDT", "LINKUSDT"):
            rules[sym] = {"minOrderQty": Decimal("0.1"), "qtyStep": Decimal("0.1"), "minNotional": Decimal("5.0"), "maxOrderQty": Decimal("5000.0")}
        elif sym in ("SUIUSDT", "NEARUSDT", "APTUSDT", "ADAUSDT", "XRPUSDT", "TIAUSDT", "INJUSDT", "ARBUSDT", "OPUSDT", "RENDERUSDT"):
            rules[sym] = {"minOrderQty": Decimal("1.0"), "qtyStep": Decimal("1.0"), "minNotional": Decimal("5.0"), "maxOrderQty": Decimal("50000.0")}
        elif sym in ("DOGEUSDT", "1000PEPEUSDT"):
            rules[sym] = {"minOrderQty": Decimal("10.0"), "qtyStep": Decimal("10.0"), "minNotional": Decimal("5.0"), "maxOrderQty": Decimal("500000.0")}
        else:
            rules[sym] = {"minOrderQty": Decimal("1.0"), "qtyStep": Decimal("1.0"), "minNotional": Decimal("5.0"), "maxOrderQty": Decimal("10000.0")}
    return rules


def prepare_v314_indicators(df_kline: pd.DataFrame, lookback: int = 30, atr_period: int = 14) -> pd.DataFrame:
    """Prepares indicators for v3.1.4 (4h bars) with zero-lookahead PIT shift(1)."""
    df = df_kline.copy().sort_values("timestamp").reset_index(drop=True)
    high = df["high"]
    low = df["low"]
    close = df["close"]
    volume = df["volume"]

    df["upper"] = high.shift(1).rolling(lookback).max()
    df["lower"] = low.shift(1).rolling(lookback).min()

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1.0 / atr_period, adjust=False).mean()

    df["ema_fast"] = close.ewm(span=50, adjust=False).mean()
    df["ema_slow"] = close.ewm(span=200, adjust=False).mean()
    df["volume_sma"] = volume.rolling(20).mean()

    try:
        df["adx"] = calculate_adx(df, period=14)
    except Exception:
        df["adx"] = np.nan

    return df


def prepare_v38_indicators(df_kline: pd.DataFrame, lookback: int = 14, atr_period: int = 14) -> pd.DataFrame:
    """Prepares indicators for v3.8 (1h bars) with zero-lookahead PIT shift(1)."""
    df = df_kline.copy().sort_values("timestamp").reset_index(drop=True)
    high = df["high"]
    low = df["low"]
    close = df["close"]
    volume = df["volume"]

    df["upper"] = high.shift(1).rolling(lookback).max()
    df["lower"] = low.shift(1).rolling(lookback).min()
    df["highest_20"] = high.shift(1).rolling(20).max()
    df["lowest_20"] = low.shift(1).rolling(20).min()
    df["prev_high"] = high.shift(1)
    df["prev_low"] = low.shift(1)

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1.0 / atr_period, adjust=False).mean()

    df["ema_fast"] = close.ewm(span=50, adjust=False).mean()
    df["ema_slow"] = close.ewm(span=200, adjust=False).mean()
    df["volume_sma"] = volume.rolling(20).mean()

    try:
        df["adx"] = calculate_adx(df, period=14)
    except Exception:
        df["adx"] = np.nan

    return df


class FastTrackActivePositionV314:
    def __init__(
        self,
        symbol: str,
        direction: int,
        entry_price: Decimal,
        entry_ts: int,
        initial_stop: Decimal,
        current_stop: Decimal,
        slice_ref: PositionSlice,
    ):
        self.symbol = symbol
        self.direction = direction
        self.entry_price = entry_price
        self.entry_ts = entry_ts
        self.initial_stop = initial_stop
        self.current_stop = current_stop
        self.slice_ref = slice_ref
        self.bars_held = 0


class FastTrackActivePositionV38:
    def __init__(
        self,
        symbol: str,
        direction: int,
        entry_price: Decimal,
        entry_ts: int,
        initial_stop: Decimal,
        current_stop: Decimal,
        initial_1r_dist: Decimal,
        slice_ref: PositionSlice,
    ):
        self.symbol = symbol
        self.direction = direction
        self.entry_price = entry_price
        self.entry_ts = entry_ts
        self.initial_stop = initial_stop
        self.current_stop = current_stop
        self.initial_1r_dist = initial_1r_dist
        self.slice_ref = slice_ref
        self.stage = 0
        self.max_favor_price = entry_price
        self.inside_bar_count = 0
        self.bars_held = 0


class FastTrackV314Runner:
    """
    Dedicated runner for v3.1.4 Champion 4h.
    Enforces strict unidirectional causal pipeline:
    SIGNAL -> ORDER_INTENT -> SIMULATED_FILL -> POSITION_UPDATE.
    """
    def __init__(
        self,
        ledger: FleetFinancialLedger,
        adapter: UniversalFleetExecutionAdapter,
        run_id: str = "V314_FT003",
        module_id: str = "v3.1.4",
        base_risk_pct: Decimal = Decimal("0.010"),  # 1.0% Half-Kelly
        max_leverage: Decimal = Decimal("3.5"),
        max_concurrent_positions: int = 4,
        max_concurrent_longs: int = 2,
        max_concurrent_shorts: int = 2,
        min_adx: float = 18.0,
        atr_stop_mult: Decimal = Decimal("2.5"),
        trailing_atr_mult: Decimal = Decimal("2.5"),
        cooldown_bars: int = 3,
        bar_interval_ms: int = 14400000,  # 4 hours
    ):
        self.ledger = ledger
        self.adapter = adapter
        self.run_id = run_id
        self.module_id = module_id
        self.base_risk_pct = base_risk_pct
        self.max_leverage = max_leverage
        self.max_concurrent_positions = max_concurrent_positions
        self.max_concurrent_longs = max_concurrent_longs
        self.max_concurrent_shorts = max_concurrent_shorts
        self.min_adx = min_adx
        self.atr_stop_mult = atr_stop_mult
        self.trailing_atr_mult = trailing_atr_mult
        self.cooldown_bars = cooldown_bars
        self.bar_interval_ms = bar_interval_ms

        self.rules = get_standard_instrument_rules()
        self.scanner = MultiCoinScanner(
            max_concurrent_positions=max_concurrent_positions,
            min_adx=min_adx,
            min_volume_ratio=1.0,
            enable_btc_delta_filter=True,
        )
        self.active_positions: Dict[str, FastTrackActivePositionV314] = {}
        self.cooldown_tracker: Dict[Tuple[str, int], int] = {}
        self.raw_signals: List[Dict[str, Any]] = []
        self.ambiguity_count = 0
        self.bar_counter = 0

    def run_replay(self, dfs_4h: Dict[str, pd.DataFrame], lookback_bars: int = 35) -> Dict[str, Any]:
        """Runs replay over resampled 4h candles."""
        prep_data = {sym: prepare_v314_indicators(df, lookback=30) for sym, df in dfs_4h.items()}
        prep_dicts = {sym: df.set_index("timestamp").to_dict("index") for sym, df in prep_data.items()}
        timeline = sorted(next(iter(dfs_4h.values()))["timestamp"].tolist())
        equity_series = []

        last_funding_ts = 0

        for idx in range(lookback_bars, len(timeline) - 1):
            ts = timeline[idx]
            next_ts = timeline[idx + 1]
            self.bar_counter += 1

            bar_start_ts = ts
            bar_close_ts = ts + self.bar_interval_ms
            signal_ready_ts = bar_close_ts + 1
            data_received_ts = bar_close_ts + 5
            order_submit_ts = bar_close_ts + 10
            fill_ts = bar_close_ts + 100  # corresponds to open of bar t+1

            bars = {}
            next_open = {}
            for sym, pdict in prep_dicts.items():
                if ts in pdict:
                    bars[sym] = pdict[ts]
                if next_ts in pdict:
                    next_open[sym] = Decimal(str(pdict[next_ts]["open"]))

            # BTC Trend Status for Delta Filter
            btc_bullish = True
            btc_bearish = True
            if "BTCUSDT" in bars:
                b_btc = bars["BTCUSDT"]
                if np.isfinite(b_btc.get("ema_fast", np.nan)) and np.isfinite(b_btc.get("ema_slow", np.nan)):
                    btc_bullish = b_btc["ema_fast"] > b_btc["ema_slow"]
                    btc_bearish = b_btc["ema_fast"] < b_btc["ema_slow"]

            # -------------------------------------------------------------
            # STEP A: Manage Existing Open Positions Against Bar t (or next open gap)
            # -------------------------------------------------------------
            for sym in list(self.active_positions.keys()):
                if sym not in bars or sym not in next_open:
                    continue
                pos = self.active_positions[sym]
                pos.bars_held += 1
                b = bars[sym]
                high = Decimal(str(b["high"]))
                low = Decimal(str(b["low"]))
                close = Decimal(str(b["close"]))
                atr = Decimal(str(b["atr"]))
                nxt_open = next_open[sym]

                # Check Stop condition
                stop_hit = False
                exit_fill_price = Decimal("0.0")
                exit_reason = ""

                if pos.direction == 1:
                    # Long stop condition: low touched current_stop or gap down at next open
                    if low <= pos.current_stop or nxt_open <= pos.current_stop:
                        stop_hit = True
                        base_p = min(nxt_open, pos.current_stop)
                        sp = Decimal("0.0002") if sym in ("BTCUSDT", "ETHUSDT") else Decimal("0.0004")
                        exit_fill_price = base_p * (Decimal("1") - sp)
                        exit_reason = "TRAILING_STOP" if pos.current_stop > pos.initial_stop else "STOP_LOSS"
                else:
                    # Short stop condition: high touched current_stop or gap up at next open
                    if high >= pos.current_stop or nxt_open >= pos.current_stop:
                        stop_hit = True
                        base_p = max(nxt_open, pos.current_stop)
                        sp = Decimal("0.0002") if sym in ("BTCUSDT", "ETHUSDT") else Decimal("0.0004")
                        exit_fill_price = base_p * (Decimal("1") + sp)
                        exit_reason = "TRAILING_STOP" if pos.current_stop < pos.initial_stop else "STOP_LOSS"

                if stop_hit:
                    # Emit OrderIntent for STOP exit
                    side = OrderSide.SELL if pos.direction == 1 else OrderSide.BUY
                    intent = OrderIntent(
                        intent_id=f"INT_V314_OUT_{sym}_{ts}",
                        run_id=self.run_id,
                        module_id=self.module_id,
                        strategy_version="3.1.4",
                        symbol=sym,
                        side=side,
                        order_type=OrderType.STOP_MARKET,
                        requested_qty=pos.slice_ref.qty,
                        limit_price=None,
                        signal_mid_price=pos.current_stop,
                        signal_ts_ms=signal_ready_ts,
                        data_received_ts_ms=data_received_ts,
                        intent_reason=exit_reason,
                        bar_start_ts_ms=bar_start_ts,
                        bar_close_ts_ms=bar_close_ts,
                        signal_ready_ts_ms=signal_ready_ts,
                        order_submit_ts_ms=order_submit_ts,
                    )
                    self.adapter.submit_intent(intent)

                    # Execute fill
                    fill = self.adapter.execute_order(
                        intent,
                        current_bid=exit_fill_price,
                        current_ask=exit_fill_price,
                        fill_ts_ms=fill_ts,
                        provenance="BYBIT_HISTORICAL_PARQUET_2024_2026",
                    )
                    # Close position slice ONLY upon confirmed fill
                    if fill is not None:
                        self.adapter.close_position_slice(pos.slice_ref, fill, exit_reason)
                        self.cooldown_tracker[(sym, pos.direction)] = self.bar_counter + self.cooldown_bars
                        del self.active_positions[sym]
                else:
                    # Ratchet trailing stop in favor of position
                    if pos.direction == 1:
                        new_stop = close - atr * self.trailing_atr_mult
                        if new_stop > pos.current_stop:
                            pos.current_stop = new_stop
                    else:
                        new_stop = close + atr * self.trailing_atr_mult
                        if new_stop < pos.current_stop:
                            pos.current_stop = new_stop

            # -------------------------------------------------------------
            # STEP B: Scan for Breakout Entry Signals on Closed Bar t
            # -------------------------------------------------------------
            candidates = []
            for sym, b in bars.items():
                cand = self.scanner.evaluate_symbol_bar(
                    symbol=sym,
                    close=float(b["close"]),
                    upper_channel=float(b.get("upper", np.nan)),
                    lower_channel=float(b.get("lower", np.nan)),
                    ema_fast=float(b.get("ema_fast", np.nan)),
                    ema_slow=float(b.get("ema_slow", np.nan)),
                    atr=float(b.get("atr", np.nan)),
                    volume=float(b.get("volume", np.nan)),
                    volume_sma=float(b.get("volume_sma", np.nan)),
                    adx=float(b.get("adx", np.nan)),
                )
                if cand:
                    candidates.append(cand)
                    # Log raw native signal for parity audit
                    self.raw_signals.append({
                        "run_id": self.run_id,
                        "module_id": self.module_id,
                        "timestamp_ms": ts,
                        "symbol": sym,
                        "direction": cand.direction,
                        "signal_type": "ENTRY",
                        "close_price": float(cand.close_price),
                        "upper": float(cand.breakout_boundary) if cand.direction == 1 else np.nan,
                        "lower": float(cand.breakout_boundary) if cand.direction == -1 else np.nan,
                        "adx": float(b.get("adx", 0.0)),
                        "tms": float(cand.trend_momentum_score),
                    })

            # Rank candidates
            ranked = self.scanner.rank_candidates(
                candidates=candidates,
                currently_held_symbols=list(self.active_positions.keys()),
                current_long_count=sum(p.direction == 1 for p in self.active_positions.values()),
                current_short_count=sum(p.direction == -1 for p in self.active_positions.values()),
                btc_bullish=btc_bullish,
                btc_bearish=btc_bearish,
                cooldown_tracker=self.cooldown_tracker,
                current_bar_idx=self.bar_counter,
            )

            # Process top ranked entries
            for cand in ranked:
                sym = cand.symbol
                if sym not in next_open:
                    continue
                rules = self.rules.get(sym)
                if not rules:
                    continue

                # Portfolio slot limits: max 4 total, max 2 long, max 2 short
                cur_longs = sum(p.direction == 1 for p in self.active_positions.values())
                cur_shorts = sum(p.direction == -1 for p in self.active_positions.values())
                if len(self.active_positions) >= self.max_concurrent_positions:
                    break
                if cand.direction == 1 and cur_longs >= self.max_concurrent_longs:
                    continue
                if cand.direction == -1 and cur_shorts >= self.max_concurrent_shorts:
                    continue

                open_p = next_open[sym]
                sp = Decimal("0.0002") if sym in ("BTCUSDT", "ETHUSDT") else Decimal("0.0004")
                if cand.direction == 1:
                    intended_fill_p = open_p * (Decimal("1") + sp)
                    init_stop = open_p - cand.atr * self.atr_stop_mult
                else:
                    intended_fill_p = open_p * (Decimal("1") - sp)
                    init_stop = open_p + cand.atr * self.atr_stop_mult

                stop_dist = abs(intended_fill_p - init_stop)
                if stop_dist <= Decimal("0.0"):
                    continue

                # Half-Kelly 1.0% position sizing
                eq = self.adapter.realized_equity
                target_risk_usdt = eq * self.base_risk_pct
                calc_qty = target_risk_usdt / stop_dist
                # Clamp to max leverage 3.5x
                max_pos_notional = eq * self.max_leverage
                max_qty = max_pos_notional / intended_fill_p
                clamped_qty = min(calc_qty, max_qty)

                # Quantize by qtyStep
                qty_step = rules["qtyStep"]
                qty = (clamped_qty // qty_step) * qty_step
                if qty < rules["minOrderQty"] or (qty * intended_fill_p) < rules["minNotional"]:
                    continue

                # Emit Entry OrderIntent
                side = OrderSide.BUY if cand.direction == 1 else OrderSide.SELL
                intent = OrderIntent(
                    intent_id=f"INT_V314_IN_{sym}_{ts}",
                    run_id=self.run_id,
                    module_id=self.module_id,
                    strategy_version="3.1.4",
                    symbol=sym,
                    side=side,
                    order_type=OrderType.MARKET,
                    requested_qty=qty,
                    limit_price=None,
                    signal_mid_price=open_p,
                    signal_ts_ms=signal_ready_ts,
                    data_received_ts_ms=data_received_ts,
                    intent_reason="DONCHIAN_4H_BREAKOUT",
                    bar_start_ts_ms=bar_start_ts,
                    bar_close_ts_ms=bar_close_ts,
                    signal_ready_ts_ms=signal_ready_ts,
                    order_submit_ts_ms=order_submit_ts,
                )
                accepted, reason = self.adapter.submit_intent(intent)
                if not accepted:
                    continue

                # Execute fill at Next Open Open_{t+1}
                bid = intended_fill_p if cand.direction == -1 else intended_fill_p * (Decimal("1") - sp)
                ask = intended_fill_p if cand.direction == 1 else intended_fill_p * (Decimal("1") + sp)
                fill = self.adapter.execute_order(
                    intent,
                    current_bid=bid,
                    current_ask=ask,
                    fill_ts_ms=fill_ts,
                    provenance="BYBIT_HISTORICAL_PARQUET_2024_2026",
                )
                if fill is not None:
                    # Update paper position ONLY upon confirmed fill
                    slice_ref = self.adapter.open_position_slice(
                        position_id=f"POS_V314_{sym}_{ts}",
                        symbol=sym,
                        direction=cand.direction,
                        fill=fill,
                    )
                    self.active_positions[sym] = FastTrackActivePositionV314(
                        symbol=sym,
                        direction=cand.direction,
                        entry_price=fill.fill_price,
                        entry_ts=fill.fill_ts_ms,
                        initial_stop=init_stop,
                        current_stop=init_stop,
                        slice_ref=slice_ref,
                    )

            # -------------------------------------------------------------
            # STEP C: Periodic Funding Settlement (Every 8h at 00:00, 08:00, 16:00 UTC)
            # -------------------------------------------------------------
            if ts % (8 * 3600 * 1000) == 0 and ts != last_funding_ts:
                last_funding_ts = ts
                for sym, pos in self.active_positions.items():
                    mark = Decimal(str(bars[sym]["close"])) if sym in bars else pos.entry_price
                    rate = Decimal("0.0001")  # 0.01% standard 8h funding
                    cashflow = (-Decimal(str(pos.direction)) * pos.slice_ref.qty * mark * rate).quantize(
                        Decimal("0.00000001"), rounding=ROUND_HALF_UP
                    )
                    evt = FundingEvent(
                        settlement_id=f"FND_V314_{sym}_{ts}",
                        symbol=sym,
                        settlement_ts_ms=ts,
                        rate=rate,
                        mark_price=mark,
                        position_qty=pos.slice_ref.qty,
                        position_direction=pos.direction,
                        cashflow=cashflow,
                    )
                    self.adapter.apply_funding(evt)

            # -------------------------------------------------------------
            # STEP D: MTM Equity Snapshot
            # -------------------------------------------------------------
            curr_prices = {s: Decimal(str(bars[s]["close"])) for s in bars}
            unrealized = sum(
                p.slice_ref.qty * (curr_prices.get(p.symbol, p.entry_price) - p.entry_price) * Decimal(str(p.direction))
                for p in self.active_positions.values()
            )
            tot_eq = self.adapter.realized_equity + unrealized
            equity_series.append(float(tot_eq))
            self.ledger.record_equity_snapshot(
                snapshot_id=f"EQ_V314_{ts}",
                run_id=self.run_id,
                module_id=self.module_id,
                timestamp_ms=ts,
                realized_equity=self.adapter.realized_equity,
                unrealized_pnl=unrealized,
                total_mtm_equity=tot_eq,
                open_positions_count=len(self.active_positions),
            )

        # Final Financial Reconciliation & Metrics
        recon = self.ledger.get_3way_financial_reconciliation(self.adapter.realized_equity)
        eq_ser = pd.Series(equity_series) if equity_series else pd.Series([float(self.ledger.initial_capital)])
        peak = eq_ser.cummax()
        dd = (peak - eq_ser) / peak * 100.0

        return {
            "run_id": self.run_id,
            "module_id": self.module_id,
            "trades_count": recon["closed_trades_count"],
            "open_positions_count": recon["open_positions_count"],
            "ending_realized_equity": float(recon["method_1_slices_realized_equity"]),
            "current_net_mtm": float(recon["method_1_slices_net_mtm"]),
            "discrepancy": float(recon["max_discrepancy"]),
            "is_penny_perfect": recon["is_penny_perfect"],
            "reconciliation_status": recon["reconciliation_status"],
            "max_drawdown_pct": round(float(dd.max()), 2),
            "ambiguity_count": self.ambiguity_count,
            "raw_signals_count": len(self.raw_signals),
        }


class FastTrackV38Runner:
    """
    Dedicated runner for v3.8 NewDragon Trend Lab (1h bars).
    Implements true Donchian 14, real Wilder ADX(14) >= 16.0 (NO mock!),
    True 1R Sizing, and 4-Stage Adaptive Exit FSM.
    """
    def __init__(
        self,
        ledger: FleetFinancialLedger,
        adapter: UniversalFleetExecutionAdapter,
        run_id: str = "V38_FT003",
        module_id: str = "v3.8",
        risk_per_trade_pct: Decimal = Decimal("0.0080"),  # 0.8% True 1R
        max_leverage: Decimal = Decimal("3.5"),
        max_concurrent_positions: int = 3,
        min_adx: float = 16.0,
        stage1_r: Decimal = Decimal("0.85"),
        stage1_lock_r: Decimal = Decimal("-0.35"),
        stage2_r: Decimal = Decimal("1.40"),
        stage2_close_pct: Decimal = Decimal("0.60"),
        stage2_lock_r: Decimal = Decimal("0.35"),
        stage3_r: Decimal = Decimal("2.00"),
        chandelier_mult: Decimal = Decimal("2.5"),
        bar_interval_ms: int = 3600000,  # 1 hour
    ):
        self.ledger = ledger
        self.adapter = adapter
        self.run_id = run_id
        self.module_id = module_id
        self.risk_per_trade_pct = risk_per_trade_pct
        self.max_leverage = max_leverage
        self.max_concurrent_positions = max_concurrent_positions
        self.min_adx = min_adx
        self.stage1_r = stage1_r
        self.stage1_lock_r = stage1_lock_r
        self.stage2_r = stage2_r
        self.stage2_close_pct = stage2_close_pct
        self.stage2_lock_r = stage2_lock_r
        self.stage3_r = stage3_r
        self.chandelier_mult = chandelier_mult
        self.bar_interval_ms = bar_interval_ms

        self.rules = get_standard_instrument_rules()
        self.scanner = MultiCoinScanner(
            max_concurrent_positions=max_concurrent_positions,
            min_adx=min_adx,
            min_volume_ratio=1.05,
            enable_btc_delta_filter=True,
        )
        self.active_positions: Dict[str, FastTrackActivePositionV38] = {}
        self.cooldown_tracker: Dict[Tuple[str, int], int] = {}
        self.raw_signals: List[Dict[str, Any]] = []
        self.ambiguity_count = 0
        self.bar_counter = 0

    def run_replay(self, dfs_1h: Dict[str, pd.DataFrame], lookback_bars: int = 50) -> Dict[str, Any]:
        """Runs replay over 1h candles."""
        prep_data = {sym: prepare_v38_indicators(df, lookback=14) for sym, df in dfs_1h.items()}
        prep_dicts = {sym: df.set_index("timestamp").to_dict("index") for sym, df in prep_data.items()}
        timeline = sorted(next(iter(dfs_1h.values()))["timestamp"].tolist())
        equity_series = []

        last_funding_ts = 0

        for idx in range(lookback_bars, len(timeline) - 1):
            ts = timeline[idx]
            next_ts = timeline[idx + 1]
            self.bar_counter += 1

            bar_start_ts = ts
            bar_close_ts = ts + self.bar_interval_ms
            signal_ready_ts = bar_close_ts + 1
            data_received_ts = bar_close_ts + 5
            order_submit_ts = bar_close_ts + 10
            fill_ts = bar_close_ts + 100  # corresponds to open of bar t+1

            bars = {}
            next_open = {}
            for sym, pdict in prep_dicts.items():
                if ts in pdict:
                    bars[sym] = pdict[ts]
                if next_ts in pdict:
                    next_open[sym] = Decimal(str(pdict[next_ts]["open"]))

            # BTC Trend Status for Delta Filter
            btc_bullish = True
            btc_bearish = True
            if "BTCUSDT" in bars:
                b_btc = bars["BTCUSDT"]
                if np.isfinite(b_btc.get("ema_fast", np.nan)) and np.isfinite(b_btc.get("ema_slow", np.nan)):
                    btc_bullish = b_btc["ema_fast"] > b_btc["ema_slow"]
                    btc_bearish = b_btc["ema_fast"] < b_btc["ema_slow"]

            # -------------------------------------------------------------
            # STEP A: Manage Existing Positions Against Bar t (FSM Transitions)
            # -------------------------------------------------------------
            for sym in list(self.active_positions.keys()):
                if sym not in bars or sym not in next_open:
                    continue
                pos = self.active_positions[sym]
                pos.bars_held += 1
                b = bars[sym]
                high = Decimal(str(b["high"]))
                low = Decimal(str(b["low"]))
                close = Decimal(str(b["close"]))
                atr = Decimal(str(b["atr"]))
                nxt_open = next_open[sym]
                prev_high = Decimal(str(b.get("prev_high", high)))
                prev_low = Decimal(str(b.get("prev_low", low)))
                highest_20 = Decimal(str(b.get("highest_20", high)))
                lowest_20 = Decimal(str(b.get("lowest_20", low)))

                # Update max favorable price
                if pos.direction == 1:
                    pos.max_favor_price = max(pos.max_favor_price, high)
                    unrealized_dist = close - pos.entry_price
                else:
                    pos.max_favor_price = min(pos.max_favor_price, low)
                    unrealized_dist = pos.entry_price - close
                unrealized_r = unrealized_dist / pos.initial_1r_dist

                # Target price for Stage 2 partial TP
                if pos.direction == 1:
                    tp2_target_p = pos.entry_price + pos.initial_1r_dist * self.stage2_r
                    stop_touched = (low <= pos.current_stop) or (nxt_open <= pos.current_stop)
                    tp_touched = (high >= tp2_target_p) and (pos.stage < 2)
                else:
                    tp2_target_p = pos.entry_price - pos.initial_1r_dist * self.stage2_r
                    stop_touched = (high >= pos.current_stop) or (nxt_open >= pos.current_stop)
                    tp_touched = (low <= tp2_target_p) and (pos.stage < 2)

                # Ambiguity Check: Both Stop and TP touched in same bar
                if stop_touched and tp_touched:
                    self.ambiguity_count += 1
                    # CONSERVATIVE RESOLUTION: Execute Stop First!
                    stop_touched = True
                    tp_touched = False

                if stop_touched:
                    # Full Stop Out
                    sp = Decimal("0.0002") if sym in ("BTCUSDT", "ETHUSDT") else Decimal("0.0004")
                    if pos.direction == 1:
                        base_p = min(nxt_open, pos.current_stop)
                        exit_fill_price = base_p * (Decimal("1") - sp)
                    else:
                        base_p = max(nxt_open, pos.current_stop)
                        exit_fill_price = base_p * (Decimal("1") + sp)

                    exit_reason = "STOP_LOSS" if pos.current_stop == pos.initial_stop else "TRAILING_STOP"
                    side = OrderSide.SELL if pos.direction == 1 else OrderSide.BUY
                    intent = OrderIntent(
                        intent_id=f"INT_V38_OUT_{sym}_{ts}",
                        run_id=self.run_id,
                        module_id=self.module_id,
                        strategy_version="3.8.0",
                        symbol=sym,
                        side=side,
                        order_type=OrderType.STOP_MARKET,
                        requested_qty=pos.slice_ref.qty,
                        limit_price=None,
                        signal_mid_price=pos.current_stop,
                        signal_ts_ms=signal_ready_ts,
                        data_received_ts_ms=data_received_ts,
                        intent_reason=exit_reason,
                        bar_start_ts_ms=bar_start_ts,
                        bar_close_ts_ms=bar_close_ts,
                        signal_ready_ts_ms=signal_ready_ts,
                        order_submit_ts_ms=order_submit_ts,
                    )
                    self.adapter.submit_intent(intent)
                    fill = self.adapter.execute_order(
                        intent,
                        current_bid=exit_fill_price,
                        current_ask=exit_fill_price,
                        fill_ts_ms=fill_ts,
                        provenance="BYBIT_HISTORICAL_PARQUET_2024_2026",
                    )
                    if fill is not None:
                        self.adapter.close_position_slice(pos.slice_ref, fill, exit_reason)
                        self.cooldown_tracker[(sym, pos.direction)] = self.bar_counter + 3
                        del self.active_positions[sym]
                        continue

                # Stage 2 Maker Partial Take Profit (60%)
                if tp_touched and pos.stage < 2:
                    pos.stage = 2
                    close_qty = (pos.slice_ref.qty * self.stage2_close_pct).quantize(
                        self.rules[sym]["qtyStep"], rounding=ROUND_FLOOR
                    )
                    if close_qty >= self.rules[sym]["minOrderQty"]:
                        side = OrderSide.SELL if pos.direction == 1 else OrderSide.BUY
                        intent = OrderIntent(
                            intent_id=f"INT_V38_TP_{sym}_{ts}",
                            run_id=self.run_id,
                            module_id=self.module_id,
                            strategy_version="3.8.0",
                            symbol=sym,
                            side=side,
                            order_type=OrderType.LIMIT_MAKER,
                            requested_qty=close_qty,
                            limit_price=tp2_target_p,
                            signal_mid_price=tp2_target_p,
                            signal_ts_ms=signal_ready_ts,
                            data_received_ts_ms=data_received_ts,
                            intent_reason="STAGE2_MAKER_TP_60PCT",
                            bar_start_ts_ms=bar_start_ts,
                            bar_close_ts_ms=bar_close_ts,
                            signal_ready_ts_ms=signal_ready_ts,
                            order_submit_ts_ms=order_submit_ts,
                        )
                        self.adapter.submit_intent(intent)
                        # Maker fill requires touch
                        fill = self.adapter.execute_order(
                            intent,
                            current_bid=tp2_target_p,
                            current_ask=tp2_target_p,
                            fill_ts_ms=fill_ts,
                            current_low=low,
                            current_high=high,
                            provenance="BYBIT_HISTORICAL_PARQUET_2024_2026",
                        )
                        if fill is not None:
                            self.adapter.close_position_slice_partial(pos.slice_ref, fill, close_qty, "STAGE2_MAKER_TP")
                            # Lock remaining stop to guaranteed profit (+0.35R)
                            if pos.direction == 1:
                                pos.current_stop = pos.entry_price + pos.initial_1r_dist * self.stage2_lock_r
                            else:
                                pos.current_stop = pos.entry_price - pos.initial_1r_dist * self.stage2_lock_r

                # Stage 3 Chandelier Trailing Stop upon reaching +2.00R
                if unrealized_r >= self.stage3_r or pos.stage == 3:
                    pos.stage = 3
                    if pos.direction == 1:
                        cand_stop = highest_20 - self.chandelier_mult * atr
                        if cand_stop > pos.current_stop:
                            pos.current_stop = cand_stop
                    else:
                        cand_stop = lowest_20 + self.chandelier_mult * atr
                        if cand_stop < pos.current_stop:
                            pos.current_stop = cand_stop

                # Stage 1 Ratchet Stop upon reaching +0.85R
                elif unrealized_r >= self.stage1_r and pos.stage < 1:
                    pos.stage = 1
                    if pos.direction == 1:
                        pos.current_stop = pos.entry_price + pos.initial_1r_dist * self.stage1_lock_r
                    else:
                        pos.current_stop = pos.entry_price - pos.initial_1r_dist * self.stage1_lock_r

                # Stage 0 Fast Scratch: 2 consecutive inside bars
                if high <= prev_high and low >= prev_low:
                    pos.inside_bar_count += 1
                else:
                    pos.inside_bar_count = 0
                if pos.stage == 0 and pos.inside_bar_count >= 2 and unrealized_r < Decimal("0.60") and pos.bars_held >= 3:
                    # Scratch exit
                    side = OrderSide.SELL if pos.direction == 1 else OrderSide.BUY
                    intent = OrderIntent(
                        intent_id=f"INT_V38_SCRATCH_{sym}_{ts}",
                        run_id=self.run_id,
                        module_id=self.module_id,
                        strategy_version="3.8.0",
                        symbol=sym,
                        side=side,
                        order_type=OrderType.MARKET,
                        requested_qty=pos.slice_ref.qty,
                        limit_price=None,
                        signal_mid_price=close,
                        signal_ts_ms=signal_ready_ts,
                        data_received_ts_ms=data_received_ts,
                        intent_reason="STAGE0_FAST_SCRATCH",
                        bar_start_ts_ms=bar_start_ts,
                        bar_close_ts_ms=bar_close_ts,
                        signal_ready_ts_ms=signal_ready_ts,
                        order_submit_ts_ms=order_submit_ts,
                    )
                    self.adapter.submit_intent(intent)
                    sp = Decimal("0.0002") if sym in ("BTCUSDT", "ETHUSDT") else Decimal("0.0004")
                    fill_p = close * (Decimal("1") - sp) if pos.direction == 1 else close * (Decimal("1") + sp)
                    fill = self.adapter.execute_order(
                        intent,
                        current_bid=fill_p,
                        current_ask=fill_p,
                        fill_ts_ms=fill_ts,
                        provenance="BYBIT_HISTORICAL_PARQUET_2024_2026",
                    )
                    if fill is not None:
                        self.adapter.close_position_slice(pos.slice_ref, fill, "STAGE0_FAST_SCRATCH")
                        self.cooldown_tracker[(sym, pos.direction)] = self.bar_counter + 2
                        del self.active_positions[sym]

            # -------------------------------------------------------------
            # STEP B: Scan for Breakout Entry Signals on Closed Bar t
            # -------------------------------------------------------------
            candidates = []
            for sym, b in bars.items():
                cand = self.scanner.evaluate_symbol_bar(
                    symbol=sym,
                    close=float(b["close"]),
                    upper_channel=float(b.get("upper", np.nan)),
                    lower_channel=float(b.get("lower", np.nan)),
                    ema_fast=float(b.get("ema_fast", np.nan)),
                    ema_slow=float(b.get("ema_slow", np.nan)),
                    atr=float(b.get("atr", np.nan)),
                    volume=float(b.get("volume", np.nan)),
                    volume_sma=float(b.get("volume_sma", np.nan)),
                    adx=float(b.get("adx", np.nan)),
                )
                if cand:
                    candidates.append(cand)
                    self.raw_signals.append({
                        "run_id": self.run_id,
                        "module_id": self.module_id,
                        "timestamp_ms": ts,
                        "symbol": sym,
                        "direction": cand.direction,
                        "signal_type": "ENTRY",
                        "close_price": float(cand.close_price),
                        "upper": float(cand.breakout_boundary) if cand.direction == 1 else np.nan,
                        "lower": float(cand.breakout_boundary) if cand.direction == -1 else np.nan,
                        "adx": float(b.get("adx", 0.0)),
                        "tms": float(cand.trend_momentum_score),
                    })

            ranked = self.scanner.rank_candidates(
                candidates=candidates,
                currently_held_symbols=list(self.active_positions.keys()),
                current_long_count=sum(p.direction == 1 for p in self.active_positions.values()),
                current_short_count=sum(p.direction == -1 for p in self.active_positions.values()),
                btc_bullish=btc_bullish,
                btc_bearish=btc_bearish,
                cooldown_tracker=self.cooldown_tracker,
                current_bar_idx=self.bar_counter,
            )

            for cand in ranked:
                sym = cand.symbol
                if sym not in next_open:
                    continue
                rules = self.rules.get(sym)
                if not rules:
                    continue

                if len(self.active_positions) >= self.max_concurrent_positions:
                    break

                open_p = next_open[sym]
                sp = Decimal("0.0002") if sym in ("BTCUSDT", "ETHUSDT") else Decimal("0.0004")
                if cand.direction == 1:
                    intended_fill_p = open_p * (Decimal("1") + sp)
                    init_stop = open_p - cand.atr * Decimal("2.0")
                else:
                    intended_fill_p = open_p * (Decimal("1") - sp)
                    init_stop = open_p + cand.atr * Decimal("2.0")

                stop_dist = abs(intended_fill_p - init_stop)
                if stop_dist <= Decimal("0.0"):
                    continue

                # True 1R Sizing Profile (0.8% risk per trade, max 3.5x leverage)
                eq = self.adapter.realized_equity
                target_risk_usdt = eq * self.risk_per_trade_pct
                calc_qty = target_risk_usdt / stop_dist
                max_pos_notional = eq * self.max_leverage
                max_qty = max_pos_notional / intended_fill_p
                clamped_qty = min(calc_qty, max_qty)

                qty_step = rules["qtyStep"]
                qty = (clamped_qty // qty_step) * qty_step
                if qty < rules["minOrderQty"] or (qty * intended_fill_p) < rules["minNotional"]:
                    continue

                side = OrderSide.BUY if cand.direction == 1 else OrderSide.SELL
                intent = OrderIntent(
                    intent_id=f"INT_V38_IN_{sym}_{ts}",
                    run_id=self.run_id,
                    module_id=self.module_id,
                    strategy_version="3.8.0",
                    symbol=sym,
                    side=side,
                    order_type=OrderType.MARKET,
                    requested_qty=qty,
                    limit_price=None,
                    signal_mid_price=open_p,
                    signal_ts_ms=signal_ready_ts,
                    data_received_ts_ms=data_received_ts,
                    intent_reason="DONCHIAN_1H_BREAKOUT",
                    bar_start_ts_ms=bar_start_ts,
                    bar_close_ts_ms=bar_close_ts,
                    signal_ready_ts_ms=signal_ready_ts,
                    order_submit_ts_ms=order_submit_ts,
                )
                accepted, reason = self.adapter.submit_intent(intent)
                if not accepted:
                    continue

                bid = intended_fill_p if cand.direction == -1 else intended_fill_p * (Decimal("1") - sp)
                ask = intended_fill_p if cand.direction == 1 else intended_fill_p * (Decimal("1") + sp)
                fill = self.adapter.execute_order(
                    intent,
                    current_bid=bid,
                    current_ask=ask,
                    fill_ts_ms=fill_ts,
                    provenance="BYBIT_HISTORICAL_PARQUET_2024_2026",
                )
                if fill is not None:
                    slice_ref = self.adapter.open_position_slice(
                        position_id=f"POS_V38_{sym}_{ts}",
                        symbol=sym,
                        direction=cand.direction,
                        fill=fill,
                    )
                    self.active_positions[sym] = FastTrackActivePositionV38(
                        symbol=sym,
                        direction=cand.direction,
                        entry_price=fill.fill_price,
                        entry_ts=fill.fill_ts_ms,
                        initial_stop=init_stop,
                        current_stop=init_stop,
                        initial_1r_dist=stop_dist,
                        slice_ref=slice_ref,
                    )

            # -------------------------------------------------------------
            # STEP C: Periodic Funding Settlement (Every 8h)
            # -------------------------------------------------------------
            if ts % (8 * 3600 * 1000) == 0 and ts != last_funding_ts:
                last_funding_ts = ts
                for sym, pos in self.active_positions.items():
                    mark = Decimal(str(bars[sym]["close"])) if sym in bars else pos.entry_price
                    rate = Decimal("0.0001")
                    cashflow = (-Decimal(str(pos.direction)) * pos.slice_ref.qty * mark * rate).quantize(
                        Decimal("0.00000001"), rounding=ROUND_HALF_UP
                    )
                    evt = FundingEvent(
                        settlement_id=f"FND_V38_{sym}_{ts}",
                        symbol=sym,
                        settlement_ts_ms=ts,
                        rate=rate,
                        mark_price=mark,
                        position_qty=pos.slice_ref.qty,
                        position_direction=pos.direction,
                        cashflow=cashflow,
                    )
                    self.adapter.apply_funding(evt)

            # -------------------------------------------------------------
            # STEP D: MTM Equity Snapshot
            # -------------------------------------------------------------
            curr_prices = {s: Decimal(str(bars[s]["close"])) for s in bars}
            unrealized = sum(
                p.slice_ref.qty * (curr_prices.get(p.symbol, p.entry_price) - p.entry_price) * Decimal(str(p.direction))
                for p in self.active_positions.values()
            )
            tot_eq = self.adapter.realized_equity + unrealized
            equity_series.append(float(tot_eq))
            self.ledger.record_equity_snapshot(
                snapshot_id=f"EQ_V38_{ts}",
                run_id=self.run_id,
                module_id=self.module_id,
                timestamp_ms=ts,
                realized_equity=self.adapter.realized_equity,
                unrealized_pnl=unrealized,
                total_mtm_equity=tot_eq,
                open_positions_count=len(self.active_positions),
            )

        recon = self.ledger.get_3way_financial_reconciliation(self.adapter.realized_equity)
        eq_ser = pd.Series(equity_series) if equity_series else pd.Series([float(self.ledger.initial_capital)])
        peak = eq_ser.cummax()
        dd = (peak - eq_ser) / peak * 100.0

        return {
            "run_id": self.run_id,
            "module_id": self.module_id,
            "trades_count": recon["closed_trades_count"],
            "open_positions_count": recon["open_positions_count"],
            "ending_realized_equity": float(recon["method_1_slices_realized_equity"]),
            "current_net_mtm": float(recon["method_1_slices_net_mtm"]),
            "discrepancy": float(recon["max_discrepancy"]),
            "is_penny_perfect": recon["is_penny_perfect"],
            "reconciliation_status": recon["reconciliation_status"],
            "max_drawdown_pct": round(float(dd.max()), 2),
            "ambiguity_count": self.ambiguity_count,
            "raw_signals_count": len(self.raw_signals),
        }


class FastTrackV312Runner:
    """
    Spot+Perp Data Gate for v3.12 Cross-Basis Arbitrage.
    Confirms absence of historical spot parquet tick data.
    Outputs CONDITIONAL: DATA_REQUIRED without fabricating synthetic trades.
    """
    def __init__(self, data_dir: str = "data/parquet_2024_2026"):
        self.data_dir = data_dir
        self.engine = CrossBasisEngine8096()

    def evaluate_feasibility(self) -> Dict[str, Any]:
        spot_files = list(Path(self.data_dir).glob("*_spot*.parquet")) + list(Path(self.data_dir).glob("*_ob*.parquet"))
        has_spot_historical = len(spot_files) > 0

        return {
            "module_id": "v3.12",
            "strategy_name": "Spot-Perp Cross-Basis",
            "port": 8096,
            "status": "CONDITIONAL: DATA_REQUIRED" if not has_spot_historical else "DATA_VERIFIED",
            "reason": "Missing synchronous Bybit Spot orderbook/tick historical parquet. Zero synthetic trades fabricated.",
            "trades_count": 0,
            "win_rate_pct": "N/A",
            "gross_pnl": 0.0,
            "fees_paid": 0.0,
            "net_pnl": 0.0,
            "profit_factor": "N/A",
            "max_drawdown_pct": 0.0,
            "live_stream_ready": True,
            "public_ws_url": "wss://stream.bybit.com/v5/public/spot + wss://stream.bybit.com/v5/public/linear",
        }
