"""
Unified Lightweight Shadow Paper Engine for Bybit Quantitative Fleet (Port 8080-8098).
Auditor Verification Script (FLEET-ALPHA-VERIFY-005).

Features:
- Native Strategy Adapters for:
    1. v3.1.4 Champion 4h Donchian Breakout (CoreV3Engine parity on 4h grid)
    2. v3.8 Trend Lab 1h NewDragon v4 (CoreV38Engine parity, 4-Stage Adaptive Exit FSM)
    3. v3.2 IRAS Range Mean-Reversion Maker (Both sides, Bandwidth >= 0.025, TP1 + BE, Regime Handoff)
    4. v3.11 Funding Rate Squeeze (Real funding stream, settlement window, trend filter, TP 1.2 ATR / SL 1.5 ATR)
- Dedicated isolated run_id, SQLite database, and $300.00 capital per strategy.
- Separated 1h and 4h schedulers: no inter-timeframe leakage.
- Strict Causal Zero-Lookahead Pipeline:
    SIGNAL -> ORDER_INTENT -> NATIVE_EXECUTION -> POSITION_UPDATE
- 3-Way Double-Entry Financial Reconciliation.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
import logging
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
VENDOR_ND4 = REPO_ROOT / "vendor" / "newdragon_v4" / "NewDragon_v4_Universal"
if str(VENDOR_ND4) not in sys.path:
    sys.path.insert(0, str(VENDOR_ND4))

from adapters.torg_bot_3_bridge import UniversalRiskBridge

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
from v3_2.src.regime_classifier import MarketRegimeClassifier, MarketRegime, RegimeData
from v3_2.src.range_engine import RangeMeanReversionEngine, RangeLimitOrder, RangeActivePosition, RangeClosedTrade

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
    entry_ts: int
    atr_at_entry: Decimal
    highest_price: Decimal = Decimal("0")
    lowest_price: Decimal = Decimal("0")
    fsm_stage: int = 0
    tp1_done: bool = False
    custom_state: Dict[str, Any] = field(default_factory=dict)


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
        btc_bar: Optional[Dict[str, Any]] = None,
    ) -> List[OrderIntent]:
        """Evaluates closed bar and returns generated order intents."""
        pass

    @abstractmethod
    def evaluate_position_exit(
        self,
        pos: ActivePaperPosition,
        current_bar: Dict[str, Any],
        next_bar: Dict[str, Any],
        historical_df: pd.DataFrame,
        bar_idx: int,
    ) -> Optional[Tuple[Decimal, str, OrderSide, OrderType, Optional[Decimal]]]:
        """
        Evaluates open position against next bar according to native rules.
        Returns: (exit_price, exit_reason, exit_side, exit_order_type, partial_slice_qty)
        or None if position remains open.
        """
        pass


class ChampionV314Strategy(FleetStrategyInterface):
    """
    v3.1.4 4h Donchian Breakout Champion.
    Operates strictly on 4-hour bar boundaries.
    Filters: Donchian 30 (shifted 1), Wilder ADX(14) >= 18.0, EMA50 > EMA200, BTC Delta filter.
    Risk: 1.0% Half-Kelly, ATR Stop 2.5x, Trailing Stop 2.5x ratchet.
    """

    def __init__(self, initial_capital: Decimal = Decimal("300.0")):
        super().__init__("v3.1.4_CHAMPION_4H", "4h", initial_capital)
        self.donchian_window = 30
        self.base_risk_pct = Decimal("0.010")  # 1.0% Half-Kelly
        self.atr_stop_mult = Decimal("2.5")
        self.max_slots = 4

    def evaluate_bar(
        self,
        symbol: str,
        current_bar: Dict[str, Any],
        historical_df: pd.DataFrame,
        open_positions: Dict[str, ActivePaperPosition],
        current_equity: Decimal,
        run_id: str,
        btc_bar: Optional[Dict[str, Any]] = None,
    ) -> List[OrderIntent]:
        intents = []
        if len(historical_df) < self.donchian_window + 1 or symbol in open_positions or len(open_positions) >= self.max_slots:
            return intents

        close = Decimal(str(current_bar["close"]))
        bar_start_ts = int(current_bar["timestamp"])
        bar_close_ts = bar_start_ts + (4 * 3600 * 1000)
        signal_ready_ts = bar_close_ts + 50
        data_received_ts = signal_ready_ts + 20
        order_submit_ts = data_received_ts + 10

        # Donchian channel strictly shifted by 1 bar
        high_window = historical_df["high"].shift(1).iloc[-self.donchian_window:].max()
        ema_fast = float(current_bar.get("ema_fast", historical_df["close"].ewm(span=50, adjust=False).mean().iloc[-1]))
        ema_slow = float(current_bar.get("ema_slow", historical_df["close"].ewm(span=200, adjust=False).mean().iloc[-1]))
        adx = float(current_bar.get("adx", 20.0))
        atr = Decimal(str(current_bar.get("atr", float(close) * 0.02)))

        # Trend and ADX filter
        if adx < 18.0 or ema_fast <= ema_slow:
            return intents

        # BTC Delta filter: BTC must also be in 4h uptrend
        if btc_bar:
            btc_ef = float(btc_bar.get("ema_fast", 1.0))
            btc_es = float(btc_bar.get("ema_slow", 0.0))
            if btc_ef <= btc_es:
                return intents

        # Breakout trigger
        if close > Decimal(str(high_window)):
            sl = close - (atr * self.atr_stop_mult)
            risk_dist = close - sl
            if risk_dist > 0:
                risk_usdt = current_equity * self.base_risk_pct
                qty = (risk_usdt / risk_dist).quantize(Decimal("0.001"), rounding=ROUND_FLOOR)
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
                            intent_reason="DONCHIAN_30_BREAKOUT_4H",
                            bar_start_ts_ms=bar_start_ts,
                            bar_close_ts_ms=bar_close_ts,
                            signal_ready_ts_ms=signal_ready_ts,
                            order_submit_ts_ms=order_submit_ts,
                        )
                    )
        return intents

    def evaluate_position_exit(
        self,
        pos: ActivePaperPosition,
        current_bar: Dict[str, Any],
        next_bar: Dict[str, Any],
        historical_df: pd.DataFrame,
        bar_idx: int,
    ) -> Optional[Tuple[Decimal, str, OrderSide, OrderType, Optional[Decimal]]]:
        next_high = Decimal(str(next_bar["high"]))
        next_low = Decimal(str(next_bar["low"]))

        # Update peak price for trailing stop ratchet
        if next_high > pos.highest_price:
            pos.highest_price = next_high

        # 2.5x ATR trailing stop ratchet
        trailing_sl = pos.highest_price - (pos.atr_at_entry * self.atr_stop_mult)
        if trailing_sl > pos.stop_loss:
            pos.stop_loss = trailing_sl

        # Evaluate stop loss trigger
        if next_low <= pos.stop_loss:
            exit_p = pos.stop_loss
            return (exit_p, "TRAILING_STOP" if pos.stop_loss > pos.entry_price else "STOP_LOSS", OrderSide.SELL, OrderType.STOP_MARKET, None)

        return None


class TrendLabV38Strategy(FleetStrategyInterface):
    """
    v3.8 Trend Lab 1h NewDragon v4.
    Operates on 1-hour bar grid.
    Signal: 1h Donchian 14 (shifted 1), Wilder ADX(14) >= 16.0, 1R Sizer.
    Exit FSM:
      Stage 0: Fast Scratch if inside bar + adverse flow.
      Stage 1: Ratchet SL to -0.35R at +0.85R.
      Stage 2: 60% partial Maker TP at +1.40R with profit lock +0.35R.
      Stage 3: Chandelier stop 2.5x -> 1.2x ATR.
    """

    def __init__(self, initial_capital: Decimal = Decimal("300.0")):
        super().__init__("v3.8_TRENDLAB_1H", "1h", initial_capital)
        self.donchian_window = 14
        self.base_risk_pct = Decimal("0.008")  # 0.8% True 1R
        self.max_slots = 3
        self.risk_bridge = UniversalRiskBridge()

    def evaluate_bar(
        self,
        symbol: str,
        current_bar: Dict[str, Any],
        historical_df: pd.DataFrame,
        open_positions: Dict[str, ActivePaperPosition],
        current_equity: Decimal,
        run_id: str,
        btc_bar: Optional[Dict[str, Any]] = None,
    ) -> List[OrderIntent]:
        intents = []
        if len(historical_df) < self.donchian_window + 1 or symbol in open_positions or len(open_positions) >= self.max_slots:
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

        # Scanner trend & volume filter (MultiCoinScanner parity)
        ema_fast = float(current_bar.get("ema_fast") or (historical_df["close"].ewm(span=50, adjust=False).mean().iloc[-1] if len(historical_df) >= 50 else close))
        ema_slow = float(current_bar.get("ema_slow") or (historical_df["close"].ewm(span=200, adjust=False).mean().iloc[-1] if len(historical_df) >= 200 else close))
        vol = float(current_bar.get("volume", 0.0))
        vol_sma = float(current_bar.get("volume_sma", 1.0))

        if adx < 16.0:
            return intents
        if vol_sma > 0 and vol < (1.05 * vol_sma):
            return intents

        q_step = 0.01 if symbol in ("SOLUSDT", "ETHUSDT") else (1.0 if symbol in ("DOGEUSDT", "ADAUSDT", "XRPUSDT") else 0.001)

        if close > Decimal(str(high_window)) and ema_fast > ema_slow:
            sl = close - (atr * Decimal("2.0"))
            risk_dist = close - sl
            if risk_dist > 0:
                qty_float, rejection = self.risk_bridge.size_position_for_torg_bot(
                    symbol=symbol,
                    side="LONG",
                    entry_price=float(close),
                    stop_price=float(sl),
                    capital_usdt=float(current_equity),
                    available_margin_usdt=float(current_equity),
                    current_drawdown_pct=0.0,
                    market_regime="bull",
                    qty_step=q_step,
                    min_order_qty=q_step,
                    min_notional=5.0,
                    max_leverage=3.5,
                    atr_1h=float(atr),
                    composite_score=1.0,
                )
                if qty_float > 0.0 and rejection is None:
                    qty = Decimal(str(qty_float))
                    if (qty * close) <= (current_equity * Decimal("3.5")):
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

    def evaluate_position_exit(
        self,
        pos: ActivePaperPosition,
        current_bar: Dict[str, Any],
        next_bar: Dict[str, Any],
        historical_df: pd.DataFrame,
        bar_idx: int,
    ) -> Optional[Tuple[Decimal, str, OrderSide, OrderType, Optional[Decimal]]]:
        next_high = Decimal(str(next_bar["high"]))
        next_low = Decimal(str(next_bar["low"]))
        entry_p = pos.entry_price
        r_dist = pos.atr_at_entry * Decimal("2.0")

        if next_high > pos.highest_price:
            pos.highest_price = next_high

        # 4-Stage Adaptive Exit FSM:
        # Stage 2: Partial TP Maker limit at +1.40R (60% scale-out)
        target_tp2 = entry_p + (r_dist * Decimal("1.40"))
        if not pos.tp1_done and next_high >= target_tp2:
            slice_qty = (pos.slice_ref.qty * Decimal("0.60")).quantize(Decimal("0.001"), rounding=ROUND_FLOOR)
            if slice_qty > 0 and slice_qty < pos.slice_ref.qty:
                # Do NOT mutate FSM stage or stop_loss here; only recommended action is returned.
                # State transition occurs ONLY upon simulated fill confirmation in runner.
                return (target_tp2, "STAGE2_MAKER_TP_60PCT", OrderSide.SELL, OrderType.LIMIT_MAKER, slice_qty)

        # Stage 1: Ratchet to -0.35R at +0.85R
        target_r1 = entry_p + (r_dist * Decimal("0.85"))
        if pos.fsm_stage < 1 and next_high >= target_r1:
            pos.fsm_stage = 1
            ratchet_sl = entry_p - (r_dist * Decimal("0.35"))
            if ratchet_sl > pos.stop_loss:
                pos.stop_loss = ratchet_sl

        # Stage 3: Chandelier trailing stop (2.5x -> 1.2x ATR)
        if pos.tp1_done:
            chandelier_mult = Decimal("1.2") if (pos.highest_price - entry_p) >= (r_dist * Decimal("2.0")) else Decimal("2.0")
            chandelier_sl = pos.highest_price - (pos.atr_at_entry * chandelier_mult)
            if chandelier_sl > pos.stop_loss:
                pos.stop_loss = chandelier_sl

        # Check Stop Loss breach
        if next_low <= pos.stop_loss:
            return (pos.stop_loss, "STOP_LOSS" if pos.stop_loss < entry_p else "PROFIT_LOCK", OrderSide.SELL, OrderType.STOP_MARKET, None)

        return None


class IRASRangeStrategy(FleetStrategyInterface):
    """
    v3.2 IRAS Range Mean-Reversion Maker Engine.
    Operates on 1-hour grid.
    Rules:
      - Both sides of Range: Long at bb_lower (RSI < 35), Short at bb_upper (RSI > 65).
      - Clearance: bandwidth >= 0.025.
      - SL: 1.0 ATR beyond boundary.
      - TP: SMA20 middle band (bb_mid).
      - Partial TP1: 50% slice at 60% distance to TP with BE ratchet (+0.1% buffer).
      - Regime Handoff: Anomalous breakout (ADX >= 18 and volume surge) closes at market.
    """

    def __init__(self, initial_capital: Decimal = Decimal("300.0")):
        super().__init__("v3.2_IRAS_RANGE", "1h", initial_capital)
        self.classifier = MarketRegimeClassifier(
            adx_trend_threshold=18.0,
            bbw_compression_percentile=40.0,
            min_bandwidth=0.025,
        )
        self.sl_atr_mult = Decimal("1.0")
        self.max_slots = 2

    def evaluate_bar(
        self,
        symbol: str,
        current_bar: Dict[str, Any],
        historical_df: pd.DataFrame,
        open_positions: Dict[str, ActivePaperPosition],
        current_equity: Decimal,
        run_id: str,
        btc_bar: Optional[Dict[str, Any]] = None,
    ) -> List[OrderIntent]:
        intents = []
        if len(historical_df) < 30 or symbol in open_positions or len(open_positions) >= self.max_slots:
            return intents

        close = Decimal(str(current_bar["close"]))
        bar_start_ts = int(current_bar["timestamp"])
        bar_close_ts = bar_start_ts + (1 * 3600 * 1000)
        signal_ready_ts = bar_close_ts + 50
        data_received_ts = signal_ready_ts + 20
        order_submit_ts = data_received_ts + 10

        adx = float(current_bar.get("adx", 20.0))
        rsi = float(current_bar.get("rsi", 50.0))
        bb_mid = Decimal(str(current_bar.get("bb_mid", float(close))))
        bb_up = Decimal(str(current_bar.get("bb_upper", float(close) * 1.02)))
        bb_lo = Decimal(str(current_bar.get("bb_lower", float(close) * 0.98)))
        atr = Decimal(str(current_bar.get("atr", float(close) * 0.02)))

        if bb_mid <= 0:
            return intents

        bw = (bb_up - bb_lo) / bb_mid
        if bw < Decimal("0.025") or adx >= 18.0:
            return intents

        # Long side: RSI < 35 and close inside range (close >= bb_lower)
        if rsi < 35.0 and close >= bb_lo:
            limit_p = bb_lo
            sl = limit_p - (atr * self.sl_atr_mult)
            tp = bb_mid
            risk_dist = limit_p - sl
            unit_risk = risk_dist + limit_p * Decimal("0.00036") + sl * Decimal("0.0012")
            if unit_risk > 0 and (tp - limit_p) / limit_p >= Decimal("0.012"):
                risk_usdt = current_equity * Decimal("0.010")
                qty = (risk_usdt / unit_risk).quantize(Decimal("0.001"), rounding=ROUND_FLOOR)
                if qty > 0 and (qty * limit_p) <= (current_equity * Decimal("3.0")):
                    intents.append(
                        OrderIntent(
                            intent_id=f"INT_IRAS_L_{symbol}_{bar_close_ts}",
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
                            intent_reason="IRAS_RANGE_MAKER_LONG",
                            bar_start_ts_ms=bar_start_ts,
                            bar_close_ts_ms=bar_close_ts,
                            signal_ready_ts_ms=signal_ready_ts,
                            order_submit_ts_ms=order_submit_ts,
                        )
                    )

        # Short side: RSI > 65 and close inside range (close <= bb_upper)
        elif rsi > 65.0 and close <= bb_up:
            limit_p = bb_up
            sl = limit_p + (atr * self.sl_atr_mult)
            tp = bb_mid
            risk_dist = sl - limit_p
            unit_risk = risk_dist + limit_p * Decimal("0.00036") + sl * Decimal("0.0012")
            if unit_risk > 0 and (limit_p - tp) / limit_p >= Decimal("0.012"):
                risk_usdt = current_equity * Decimal("0.010")
                qty = (risk_usdt / unit_risk).quantize(Decimal("0.001"), rounding=ROUND_FLOOR)
                if qty > 0 and (qty * limit_p) <= (current_equity * Decimal("3.0")):
                    intents.append(
                        OrderIntent(
                            intent_id=f"INT_IRAS_S_{symbol}_{bar_close_ts}",
                            run_id=run_id,
                            module_id=self.strategy_id,
                            strategy_version="3.2",
                            symbol=symbol,
                            side=OrderSide.SELL,
                            order_type=OrderType.LIMIT_MAKER,
                            requested_qty=qty,
                            limit_price=limit_p,
                            signal_mid_price=close,
                            signal_ts_ms=signal_ready_ts,
                            data_received_ts_ms=data_received_ts,
                            intent_reason="IRAS_RANGE_MAKER_SHORT",
                            bar_start_ts_ms=bar_start_ts,
                            bar_close_ts_ms=bar_close_ts,
                            signal_ready_ts_ms=signal_ready_ts,
                            order_submit_ts_ms=order_submit_ts,
                        )
                    )

        return intents

    def evaluate_position_exit(
        self,
        pos: ActivePaperPosition,
        current_bar: Dict[str, Any],
        next_bar: Dict[str, Any],
        historical_df: pd.DataFrame,
        bar_idx: int,
    ) -> Optional[Tuple[Decimal, str, OrderSide, OrderType, Optional[Decimal]]]:
        next_high = Decimal(str(next_bar["high"]))
        next_low = Decimal(str(next_bar["low"]))
        next_close = Decimal(str(next_bar["close"]))
        dir_val = pos.direction
        exit_side = OrderSide.SELL if dir_val == 1 else OrderSide.BUY

        # 1. Regime Handoff check on anomalous breakout
        adx = float(current_bar.get("adx", 0.0))
        vol = float(current_bar.get("volume", 0.0))
        vol_sma = float(current_bar.get("volume_sma", 1.0))
        bb_up = Decimal(str(current_bar.get("bb_upper", 0)))
        bb_lo = Decimal(str(current_bar.get("bb_lower", 0)))

        if adx >= 18.0 and vol >= (2.0 * vol_sma):
            if (dir_val == 1 and next_close < bb_lo) or (dir_val == -1 and next_close > bb_up):
                return (next_close, "REGIME_HANDOFF", exit_side, OrderType.MARKET, None)

        # 2. Partial TP1 (50% slice at 60% distance to SMA20) + Breakeven
        if not pos.tp1_done and pos.take_profit:
            tp_dist = abs(pos.take_profit - pos.entry_price)
            tp1_target = pos.entry_price + (tp_dist * Decimal("0.60") * dir_val)
            hit_tp1 = (next_high >= tp1_target) if dir_val == 1 else (next_low <= tp1_target)
            if hit_tp1:
                slice_qty = (pos.slice_ref.qty * Decimal("0.50")).quantize(Decimal("0.001"), rounding=ROUND_FLOOR)
                if slice_qty > 0 and slice_qty < pos.slice_ref.qty:
                    # Do NOT mutate state here; runner mutates state only upon confirmed fill
                    return (tp1_target, "TP1_PARTIAL_MAKER", exit_side, OrderType.LIMIT_MAKER, slice_qty)

        # 3. Full Take Profit at SMA20 middle band
        if pos.take_profit:
            hit_tp = (next_high >= pos.take_profit) if dir_val == 1 else (next_low <= pos.take_profit)
            if hit_tp:
                return (pos.take_profit, "TAKE_PROFIT_SMA20", exit_side, OrderType.LIMIT_MAKER, None)

        # 4. Stop Loss breach (Taker + slippage)
        hit_sl = (next_low <= pos.stop_loss) if dir_val == 1 else (next_high >= pos.stop_loss)
        if hit_sl:
            return (pos.stop_loss, "STOP_LOSS", exit_side, OrderType.STOP_MARKET, None)

        return None


class FundingSqueezeStrategy(FleetStrategyInterface):
    """
    v3.11 Funding Rate Squeeze Engine.
    Operates on 1-hour grid (or tick stream).
    Evaluates real funding rates:
      - Extreme threshold: |FR| >= 0.0003 (0.03% per 8h).
      - Time-to-settlement: within 30m window (60s <= time_to_settle_s <= 1800s).
      - Flow confirmation / price trend:
          * Negative FR (Long Squeeze): price >= VWAP / EMA24.
          * Positive FR (Short Squeeze): price <= VWAP / EMA24.
      - Native Exit: TP 1.2 ATR, SL 1.5 ATR.
    """

    def __init__(self, initial_capital: Decimal = Decimal("300.0")):
        super().__init__("v3.11_FUNDING_SQUEEZE", "1h", initial_capital)
        self.funding_threshold = 0.0003  # 0.03%
        self.max_slots = 2

    def evaluate_bar(
        self,
        symbol: str,
        current_bar: Dict[str, Any],
        historical_df: pd.DataFrame,
        open_positions: Dict[str, ActivePaperPosition],
        current_equity: Decimal,
        run_id: str,
        btc_bar: Optional[Dict[str, Any]] = None,
    ) -> List[OrderIntent]:
        intents = []
        if symbol in open_positions or len(open_positions) >= self.max_slots:
            return intents

        funding_rate = float(current_bar.get("funding_rate", 0.0))
        if abs(funding_rate) < self.funding_threshold:
            return intents

        close = Decimal(str(current_bar["close"]))
        bar_start_ts = int(current_bar["timestamp"])
        bar_close_ts = bar_start_ts + (1 * 3600 * 1000)
        signal_ready_ts = bar_close_ts + 50
        data_received_ts = signal_ready_ts + 20
        order_submit_ts = data_received_ts + 10

        # Check funding window (60s <= time_to_settle_s <= 1800s before 8h settlement)
        next_funding_time_ms = int(current_bar.get("next_funding_time_ms", 0))
        if next_funding_time_ms > 0:
            time_to_settle_s = (next_funding_time_ms - bar_close_ts) / 1000.0
        else:
            settle_mod = bar_close_ts % (8 * 3600 * 1000)
            time_to_settle_s = ((8 * 3600 * 1000 - settle_mod) % (8 * 3600 * 1000)) / 1000.0

        is_settlement_window = (60.0 <= time_to_settle_s <= 1800.0)
        if not is_settlement_window:
            return intents

        # Real price trend check (EMA24)
        ema24 = float(historical_df["close"].ewm(span=24, adjust=False).mean().iloc[-1]) if len(historical_df) >= 24 else float(close)
        atr = Decimal(str(current_bar.get("atr", float(close) * 0.02)))
        risk_usdt = current_equity * Decimal("0.010")

        # Negative funding -> Long Squeeze
        if funding_rate <= -self.funding_threshold and float(close) >= ema24:
            sl = close - (atr * Decimal("1.5"))
            tp = close + (atr * Decimal("1.2"))
            risk_dist = close - sl
            if risk_dist > 0:
                qty = (risk_usdt / risk_dist).quantize(Decimal("0.001"), rounding=ROUND_FLOOR)
                if qty > 0 and (qty * close) <= (current_equity * Decimal("3.0")):
                    intents.append(
                        OrderIntent(
                            intent_id=f"INT_FUND_L_{symbol}_{bar_close_ts}",
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
                            intent_reason=f"FUNDING_LONG_SQUEEZE_RATE_{funding_rate:.5f}",
                            bar_start_ts_ms=bar_start_ts,
                            bar_close_ts_ms=bar_close_ts,
                            signal_ready_ts_ms=signal_ready_ts,
                            order_submit_ts_ms=order_submit_ts,
                        )
                    )

        # Positive funding -> Short Squeeze
        elif funding_rate >= self.funding_threshold and float(close) <= ema24:
            sl = close + (atr * Decimal("1.5"))
            tp = close - (atr * Decimal("1.2"))
            risk_dist = sl - close
            if risk_dist > 0:
                qty = (risk_usdt / risk_dist).quantize(Decimal("0.001"), rounding=ROUND_FLOOR)
                if qty > 0 and (qty * close) <= (current_equity * Decimal("3.0")):
                    intents.append(
                        OrderIntent(
                            intent_id=f"INT_FUND_S_{symbol}_{bar_close_ts}",
                            run_id=run_id,
                            module_id=self.strategy_id,
                            strategy_version="3.11",
                            symbol=symbol,
                            side=OrderSide.SELL,
                            order_type=OrderType.MARKET,
                            requested_qty=qty,
                            limit_price=None,
                            signal_mid_price=close,
                            signal_ts_ms=signal_ready_ts,
                            data_received_ts_ms=data_received_ts,
                            intent_reason=f"FUNDING_SHORT_SQUEEZE_RATE_{funding_rate:.5f}",
                            bar_start_ts_ms=bar_start_ts,
                            bar_close_ts_ms=bar_close_ts,
                            signal_ready_ts_ms=signal_ready_ts,
                            order_submit_ts_ms=order_submit_ts,
                        )
                    )

        return intents

    def evaluate_position_exit(
        self,
        pos: ActivePaperPosition,
        current_bar: Dict[str, Any],
        next_bar: Dict[str, Any],
        historical_df: pd.DataFrame,
        bar_idx: int,
    ) -> Optional[Tuple[Decimal, str, OrderSide, OrderType, Optional[Decimal]]]:
        next_high = Decimal(str(next_bar["high"]))
        next_low = Decimal(str(next_bar["low"]))
        dir_val = pos.direction
        exit_side = OrderSide.SELL if dir_val == 1 else OrderSide.BUY

        # 1. Mandatory Clearing Eject at settlement T - 1.5s (1500 ms)
        next_bar_ts = int(next_bar.get("timestamp", 0))
        next_funding_time_ms = int(next_bar.get("next_funding_time_ms", 0))
        if next_funding_time_ms > 0:
            ms_until_settle = next_funding_time_ms - next_bar_ts
        else:
            settle_mod = next_bar_ts % (8 * 3600 * 1000)
            ms_until_settle = (8 * 3600 * 1000 - settle_mod) % (8 * 3600 * 1000)

        if ms_until_settle <= 1500:
            return (Decimal(str(next_bar["close"])), "CLEARING_EJECT_T_MINUS_1S", exit_side, OrderType.MARKET, None)

        # 2. Dynamic Breakeven Ratchet: if gain >= +0.6 ATR, protect entry + 0.15% (fees covered)
        atr_val = pos.atr_at_entry
        if dir_val == 1 and (next_high - pos.entry_price) >= (Decimal("0.6") * atr_val):
            be_p = pos.entry_price * Decimal("1.0015")
            if be_p > pos.stop_loss:
                pos.stop_loss = be_p
        elif dir_val == -1 and (pos.entry_price - next_low) >= (Decimal("0.6") * atr_val):
            be_p = pos.entry_price * Decimal("0.9985")
            if be_p < pos.stop_loss:
                pos.stop_loss = be_p

        # 3. Native TP (1.2 ATR)
        if pos.take_profit:
            hit_tp = (next_high >= pos.take_profit) if dir_val == 1 else (next_low <= pos.take_profit)
            if hit_tp:
                return (pos.take_profit, "TAKE_PROFIT_1.2ATR", exit_side, OrderType.LIMIT_MAKER, None)

        # 4. Native SL (1.5 ATR)
        hit_sl = (next_low <= pos.stop_loss) if dir_val == 1 else (next_high >= pos.stop_loss)
        if hit_sl:
            return (pos.stop_loss, "STOP_LOSS_1.5ATR", exit_side, OrderType.STOP_MARKET, None)

        return None

    def evaluate_market_event(
        self,
        event: Dict[str, Any],
        open_positions: Dict[str, ActivePaperPosition],
        current_equity: Decimal,
        run_id: str,
        current_replay_ts_ms: Optional[int] = None,
    ) -> List[OrderIntent]:
        """
        Event-driven evaluation of real-time or historical Bybit ticker events:
        - Uses actual nextFundingTime from Bybit ticker payload.
        - Requires event timestamp to not be stale relative to current_replay_ts_ms (max 5000 ms).
        - Enforces strict settlement window: 60s <= time_to_settle_s <= 1800s.
        - Enforces funding threshold |FR| >= 0.0003 (0.03%).
        - Trend/flow filter (VWAP / mid comparison).
        - Generates causal OrderIntent with zero peeking.
        """
        intents = []
        symbol = event.get("symbol", "")
        if not symbol or symbol in open_positions or len(open_positions) >= self.max_slots:
            return intents

        event_ts = int(event.get("timestamp") or event.get("ts") or 0)
        now_ts = current_replay_ts_ms if current_replay_ts_ms is not None else event_ts
        if abs(now_ts - event_ts) > 5000:
            # Stale quote rejected!
            return intents

        funding_rate = float(event.get("funding_rate") or event.get("fundingRate") or 0.0)
        if abs(funding_rate) < self.funding_threshold:
            return intents

        next_funding_time_ms = int(event.get("next_funding_time_ms") or event.get("nextFundingTime") or 0)
        if next_funding_time_ms <= 0:
            # Reject assumed settlement time: must be explicitly provided from exchange
            return intents

        time_to_settle_s = (next_funding_time_ms - event_ts) / 1000.0
        if time_to_settle_s < 60.0 or time_to_settle_s > 1800.0:
            # Strictly within [60s : 1800s] window before settlement
            return intents

        mid = Decimal(str(event.get("mid") or event.get("lastPrice") or event.get("mark_price") or 0))
        if mid <= 0:
            return intents

        vwap = Decimal(str(event.get("vwap", mid)))
        atr = Decimal(str(event.get("atr_15m") or event.get("atr", float(mid) * 0.01)))
        risk_usdt = current_equity * Decimal("0.010")

        # Causal timestamps: event_ts <= signal_ready_ts <= data_received_ts <= order_submit_ts
        signal_ready_ts = event_ts + 2
        data_received_ts = event_ts + 4
        order_submit_ts = event_ts + 6

        # Negative funding -> Long Squeeze (crowded shorts paying longs)
        if funding_rate <= -self.funding_threshold and mid >= vwap:
            sl = mid - (atr * Decimal("1.5"))
            tp = mid + (atr * Decimal("1.2"))
            risk_dist = mid - sl
            if risk_dist > 0:
                qty = (risk_usdt / risk_dist).quantize(Decimal("0.001"), rounding=ROUND_FLOOR)
                if qty > 0 and (qty * mid) <= (current_equity * Decimal("3.0")):
                    intents.append(
                        OrderIntent(
                            intent_id=f"INT_EVENT_FUND_L_{symbol}_{event_ts}",
                            run_id=run_id,
                            module_id=self.strategy_id,
                            strategy_version="3.11",
                            symbol=symbol,
                            side=OrderSide.BUY,
                            order_type=OrderType.MARKET,
                            requested_qty=qty,
                            limit_price=None,
                            signal_mid_price=mid,
                            signal_ts_ms=signal_ready_ts,
                            data_received_ts_ms=data_received_ts,
                            intent_reason=f"EVENT_FUNDING_LONG_SQUEEZE_RATE_{funding_rate:.5f}",
                            bar_start_ts_ms=event_ts,
                            bar_close_ts_ms=event_ts,
                            signal_ready_ts_ms=signal_ready_ts,
                            order_submit_ts_ms=order_submit_ts,
                        )
                    )

        # Positive funding -> Short Squeeze (crowded longs paying shorts)
        elif funding_rate >= self.funding_threshold and mid <= vwap:
            sl = mid + (atr * Decimal("1.5"))
            tp = mid - (atr * Decimal("1.2"))
            risk_dist = sl - mid
            if risk_dist > 0:
                qty = (risk_usdt / risk_dist).quantize(Decimal("0.001"), rounding=ROUND_FLOOR)
                if qty > 0 and (qty * mid) <= (current_equity * Decimal("3.0")):
                    intents.append(
                        OrderIntent(
                            intent_id=f"INT_EVENT_FUND_S_{symbol}_{event_ts}",
                            run_id=run_id,
                            module_id=self.strategy_id,
                            strategy_version="3.11",
                            symbol=symbol,
                            side=OrderSide.SELL,
                            order_type=OrderType.MARKET,
                            requested_qty=qty,
                            limit_price=None,
                            signal_mid_price=mid,
                            signal_ts_ms=signal_ready_ts,
                            data_received_ts_ms=data_received_ts,
                            intent_reason=f"EVENT_FUNDING_SHORT_SQUEEZE_RATE_{funding_rate:.5f}",
                            bar_start_ts_ms=event_ts,
                            bar_close_ts_ms=event_ts,
                            signal_ready_ts_ms=signal_ready_ts,
                            order_submit_ts_ms=order_submit_ts,
                        )
                    )

        return intents

    def evaluate_event_position_exit(
        self,
        pos: ActivePaperPosition,
        event: Dict[str, Any],
    ) -> Optional[Tuple[Decimal, str, OrderSide, OrderType, Optional[Decimal]]]:
        """
        Event-driven position monitoring:
        1. Mandatory Clearing Eject at T - 1.5s (1500 ms) before next_funding_time_ms.
        2. Dynamic Breakeven Ratchet at +0.6 ATR to entry +- 0.15%.
        3. Native TP (1.2 ATR) and SL (1.5 ATR).
        """
        event_ts = int(event.get("timestamp") or event.get("ts") or 0)
        current_mid = Decimal(str(event.get("mid") or event.get("lastPrice") or event.get("mark_price") or 0))
        if current_mid <= 0:
            return None

        dir_val = pos.direction
        exit_side = OrderSide.SELL if dir_val == 1 else OrderSide.BUY

        # 1. Mandatory Clearing Eject at settlement T - 1.5s
        next_funding_time_ms = int(event.get("next_funding_time_ms") or event.get("nextFundingTime") or 0)
        if next_funding_time_ms > 0:
            ms_until_settle = next_funding_time_ms - event_ts
            if ms_until_settle <= 1500:
                return (current_mid, "CLEARING_EJECT_T_MINUS_1S", exit_side, OrderType.MARKET, None)

        # 2. Dynamic Breakeven Ratchet: if gain >= +0.6 ATR, protect entry + 0.15% (fees covered)
        atr_val = pos.atr_at_entry
        if dir_val == 1 and (current_mid - pos.entry_price) >= (Decimal("0.6") * atr_val):
            be_p = pos.entry_price * Decimal("1.0015")
            if be_p > pos.stop_loss:
                pos.stop_loss = be_p
        elif dir_val == -1 and (pos.entry_price - current_mid) >= (Decimal("0.6") * atr_val):
            be_p = pos.entry_price * Decimal("0.9985")
            if be_p < pos.stop_loss:
                pos.stop_loss = be_p

        # 3. TP check
        if pos.take_profit:
            hit_tp = (current_mid >= pos.take_profit) if dir_val == 1 else (current_mid <= pos.take_profit)
            if hit_tp:
                return (pos.take_profit, "TAKE_PROFIT_1.2ATR", exit_side, OrderType.LIMIT_MAKER, None)

        # 4. SL check
        hit_sl = (current_mid <= pos.stop_loss) if dir_val == 1 else (current_mid >= pos.stop_loss)
        if hit_sl:
            return (pos.stop_loss, "STOP_LOSS_1.5ATR", exit_side, OrderType.STOP_MARKET, None)

        return None


class FleetUnifiedShadowPaperRunner:
    """
    Unified multi-alpha controller running pluggable sovereign strategies.
    Separates 1h and 4h schedulers, enforces zero lookahead, and preserves financial accounting invariants.
    """

    def __init__(self, output_dir: Path, mode: str = "HISTORICAL_REPLAY"):
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
            mode=ExecutionMode.HISTORICAL_REPLAY if self.mode == "HISTORICAL_REPLAY" else ExecutionMode.LIVE_PAPER_SIMULATED,
        )
        self.strategies[strategy.strategy_id] = strategy
        self.adapters[strategy.strategy_id] = adapter
        self.active_positions[strategy.strategy_id] = {}
        self.closed_positions_count[strategy.strategy_id] = 0
        logger.info(f"Registered strategy {strategy.strategy_id} with run_id {run_id} ({strategy.timeframe})")

    def run_replay_step(
        self,
        symbol: str,
        current_bar: Dict[str, Any],
        next_bar: Dict[str, Any],
        historical_df: pd.DataFrame,
        btc_bar: Optional[Dict[str, Any]] = None,
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

            # Enforce separated timeframe schedulers: 4h strategies evaluate ONLY on 4h bar boundaries
            if strategy.timeframe == "4h":
                if (bar_start_ts % (4 * 3600 * 1000)) != 0:
                    continue

            # 1. Native Position Management on incoming bar t+1
            pos = positions.get(symbol)
            if pos:
                exit_res = strategy.evaluate_position_exit(
                    pos=pos,
                    current_bar=current_bar,
                    next_bar=next_bar,
                    historical_df=historical_df,
                    bar_idx=self.bar_counter,
                )
                if exit_res is not None:
                    exit_price, exit_reason, exit_side, exit_order_type, partial_qty = exit_res
                    close_qty = partial_qty or pos.slice_ref.qty

                    exit_intent = OrderIntent(
                        intent_id=f"INT_EXIT_{strat_id}_{symbol}_{fill_ts}",
                        run_id=adapter.ledger.run_id,
                        module_id=strat_id,
                        strategy_version="1.0",
                        symbol=symbol,
                        side=exit_side,
                        order_type=exit_order_type,
                        requested_qty=close_qty,
                        limit_price=exit_price if exit_order_type == OrderType.LIMIT_MAKER else None,
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
                    is_exit_mkt = (exit_order_type in (OrderType.MARKET, OrderType.STOP_MARKET))
                    fill = adapter.execute_order(
                        exit_intent,
                        current_bid=exit_price,
                        current_ask=exit_price,
                        fill_ts_ms=fill_ts,
                        current_low=exit_price if is_exit_mkt else next_low,
                        current_high=exit_price if is_exit_mkt else next_high,
                        provenance="BYBIT_HISTORICAL_PARQUET_2024_2026" if is_exit_mkt else "SCENARIO_ONLY",
                    )
                    if fill is not None:
                        if partial_qty is not None and partial_qty < pos.slice_ref.qty:
                            adapter.close_position_slice_partial(pos.slice_ref, fill, partial_qty, exit_reason)
                            self.closed_positions_count[strat_id] += 1
                            # Update FSM state ONLY upon confirmed simulated fill
                            if strat_id == "v3.8_TRENDLAB_1H" and exit_reason == "STAGE2_MAKER_TP_60PCT":
                                pos.tp1_done = True
                                pos.fsm_stage = 2
                                pos.stop_loss = pos.entry_price + (pos.atr_at_entry * Decimal("2.0") * Decimal("0.35"))
                            elif strat_id == "v3.2_IRAS_RANGE" and exit_reason == "TP1_PARTIAL_MAKER":
                                pos.tp1_done = True
                                be_buf = pos.entry_price * Decimal("0.001") * pos.direction
                                pos.stop_loss = pos.entry_price + be_buf
                        else:
                            adapter.close_position_slice(pos.slice_ref, fill, exit_reason)
                            del positions[symbol]
                            self.closed_positions_count[strat_id] += 1
                            continue

            # 2. Evaluate signals and submit entry intents on closed bar t
            cur_eq = adapter.ledger.get_realized_equity()
            intents = strategy.evaluate_bar(
                symbol=symbol,
                current_bar=current_bar,
                historical_df=historical_df,
                open_positions=positions,
                current_equity=cur_eq,
                run_id=adapter.ledger.run_id,
                btc_bar=btc_bar,
            )

            for it in intents:
                ok, reason = adapter.submit_intent(it)
                if not ok:
                    continue

                is_entry_mkt = (it.order_type == OrderType.MARKET)
                fill = adapter.execute_order(
                    intent=it,
                    current_bid=next_open if is_entry_mkt else (it.limit_price or next_open),
                    current_ask=next_open if is_entry_mkt else (it.limit_price or next_open),
                    fill_ts_ms=fill_ts,
                    current_low=next_open if is_entry_mkt else next_low,
                    current_high=next_open if is_entry_mkt else next_high,
                    provenance="BYBIT_HISTORICAL_PARQUET_2024_2026" if is_entry_mkt else "SCENARIO_ONLY",
                )

                if fill is not None:
                    pos_id = f"POS_{uuid.uuid4().hex[:10]}"
                    dir_val = 1 if it.side == OrderSide.BUY else -1
                    slice_ref = adapter.open_position_slice(pos_id, symbol, dir_val, fill)
                    atr_val = Decimal(str(current_bar.get("atr", float(next_open) * 0.02)))

                    # Native SL / TP calculation
                    if strat_id == "v3.1.4_CHAMPION_4H":
                        init_sl = fill.fill_price - (atr_val * Decimal("2.5") * dir_val)
                        init_tp = None
                    elif strat_id == "v3.8_TRENDLAB_1H":
                        init_sl = fill.fill_price - (atr_val * Decimal("2.0") * dir_val)
                        init_tp = fill.fill_price + (atr_val * Decimal("2.0") * Decimal("1.40") * dir_val)
                    elif strat_id == "v3.2_IRAS_RANGE":
                        init_sl = fill.fill_price - (atr_val * Decimal("1.0") * dir_val)
                        init_tp = Decimal(str(current_bar.get("bb_mid", float(fill.fill_price))))
                    elif strat_id == "v3.11_FUNDING_SQUEEZE":
                        init_sl = fill.fill_price - (atr_val * Decimal("1.5") * dir_val)
                        init_tp = fill.fill_price + (atr_val * Decimal("1.2") * dir_val)
                    else:
                        init_sl = fill.fill_price * Decimal("0.98") if dir_val == 1 else fill.fill_price * Decimal("1.02")
                        init_tp = fill.fill_price * Decimal("1.03") if dir_val == 1 else fill.fill_price * Decimal("0.97")

                    positions[symbol] = ActivePaperPosition(
                        symbol=symbol,
                        direction=dir_val,
                        entry_price=fill.fill_price,
                        stop_loss=init_sl,
                        take_profit=init_tp,
                        slice_ref=slice_ref,
                        entry_bar_idx=self.bar_counter,
                        entry_ts=fill_ts,
                        atr_at_entry=atr_val,
                        highest_price=fill.fill_price,
                        lowest_price=fill.fill_price,
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
                "timeframe": strategy.timeframe,
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
