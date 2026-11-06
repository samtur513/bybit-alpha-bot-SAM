"""
Core Engine v3.8: NewDragon Trend Lab.
Integrates NewDragon v4.0 Universal Phase 1 into Bybit Linear Perpetuals:
1. UniversalRiskBridge: True 1R Position Sizer with Fractional Kelly, CVaR 95%,
   and Micro-Account Router (strict minNotional enforcement).
2. AdaptiveExitBridge: 4-Stage Regime-Adaptive Exit State Machine:
   - Stage 0: Fast Scratch on inside bars + adverse OFI
   - Stage 1: Ratchet stop to -0.35R at +0.85R
   - Stage 2: 60% Partial Scale-Out via Maker Limit order at +1.40R (Maker fee 0.0324%)
              and guaranteed profit lock (+0.35R)
   - Stage 3: Chandelier Trailing Stop (2.5x ATR, dynamic compression to 1.2x ATR)
3. Zero-Lookahead Point-in-Time execution and Penny-Perfect trade accounting.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR
import logging
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Set, Tuple
import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VENDOR_ND4 = REPO_ROOT / "vendor" / "newdragon_v4" / "NewDragon_v4_Universal"
if str(VENDOR_ND4) not in sys.path:
    sys.path.insert(0, str(VENDOR_ND4))

from adapters.torg_bot_3_bridge import UniversalRiskBridge, AdaptiveExitBridge
from core.models import PositionTracker, ExitAction
from v3.src.core_v3 import ClosedTrade, ActivePosition, PortfolioSnapshot
from v3.src.multi_scanner import MultiCoinScanner, CoinCandidate
from v3_8.src.config import (
    INITIAL_CAPITAL,
    TAKER_FEE,
    MAKER_FEE,
    SLIPPAGE,
    MAX_LEVERAGE,
    MAX_PORTFOLIO_HEAT_PCT,
    MAX_CONCURRENT_POSITIONS,
    STAGE1_R,
    STAGE1_LOCK_R,
    STAGE2_R,
    STAGE2_CLOSE_PCT,
    STAGE2_LOCK_R,
    STAGE3_R,
    CHANDELIER_MULT,
    CHANDELIER_COMPRESSED_MULT,
    DONCHIAN_WINDOW,
    ADX_THRESHOLD,
    BAR_INTERVAL_MS,
)

logger = logging.getLogger("CoreV38Engine")


@dataclass
class PositionTrackerV38:
    """Wrapper tracking NewDragon FSM state along with Bybit active position."""
    position: ActivePosition
    tracker: PositionTracker
    target_tp_maker: Decimal
    is_tp_filled: bool = False
    stage_name: str = "Stage 0: Initial"


class CoreV38Engine:
    """
    NewDragon Trend Lab Engine combining 1h Donchian Breakout with
    NewDragon v4 True 1R Sizer and 4-Stage Adaptive Exit State Machine.
    """

    def __init__(
        self,
        initial_capital: Decimal = INITIAL_CAPITAL,
        instrument_rules: Optional[Dict[str, Dict[str, Decimal]]] = None,
        taker_fee: Decimal = TAKER_FEE,
        maker_fee: Decimal = MAKER_FEE,
        slippage: Decimal = SLIPPAGE,
        max_leverage: Decimal = Decimal(str(MAX_LEVERAGE)),
        max_concurrent_positions: int = MAX_CONCURRENT_POSITIONS,
        bar_interval_ms: int = BAR_INTERVAL_MS,
    ):
        self.initial_capital = initial_capital
        self.realized_equity = initial_capital
        self.peak_equity = initial_capital
        self.max_drawdown_pct = Decimal("0")
        self.trading_halted = False
        self.bar_counter = 0
        self.last_bar_timestamp = 0
        self.bar_interval_ms = bar_interval_ms
        self.events: List[Dict[str, Any]] = []

        self.instrument_rules = instrument_rules or {}
        self.cooldown_tracker: Dict[str, int] = {}
        self.funding_receipts: Dict[str, int] = {}
        self.cooldown_bars = 3

        self.taker_fee = taker_fee
        self.maker_fee = maker_fee
        self.slippage = slippage
        self.max_leverage = max_leverage
        self.max_concurrent_positions = max_concurrent_positions

        # NewDragon v4 Bridges
        self.risk_bridge = UniversalRiskBridge(
            taker_fee=float(taker_fee),
            maker_fee=float(maker_fee),
            slippage_base=float(slippage),
            max_leverage=float(max_leverage),
        )
        self.exit_bridge = AdaptiveExitBridge()

        # Multi-coin candidate scanner
        self.scanner = MultiCoinScanner(
            max_concurrent_positions=max_concurrent_positions,
            min_adx=float(ADX_THRESHOLD),
            min_volume_ratio=1.05,
            enable_btc_delta_filter=True,
        )

        self.open_positions: Dict[str, ActivePosition] = {}
        self.trackers: Dict[str, PositionTrackerV38] = {}
        self.closed_trades: List[ClosedTrade] = []
        self.equity_history: List[Tuple[int, Decimal]] = []
        self.fsm_stage_stats = {"STAGE0": 0, "STAGE1": 0, "STAGE2": 0, "STAGE3": 0}

    def get_equity(self, current_prices: Dict[str, Decimal]) -> Decimal:
        """Calculates current mark-to-market equity."""
        unrealized = Decimal("0")
        for sym, pos in self.open_positions.items():
            mark = current_prices.get(sym, pos.entry_price)
            if pos.direction == 1:
                gross = pos.current_qty * (mark - pos.entry_price)
            else:
                gross = pos.current_qty * (pos.entry_price - mark)
            est_fee = pos.current_qty * mark * self.taker_fee
            unrealized += gross - est_fee
        return self.realized_equity + unrealized

    def get_available_margin(self, current_prices: Dict[str, Decimal]) -> Decimal:
        """Calculates available margin under max_leverage constraint."""
        eq = self.get_equity(current_prices)
        if eq <= Decimal("0"):
            return Decimal("0")
        used_margin = sum(
            (pos.current_qty * current_prices.get(sym, pos.entry_price)) / self.max_leverage
            for sym, pos in self.open_positions.items()
        )
        return max(Decimal("0"), eq - used_margin)

    def _observe(self, prices: Dict[str, Decimal]) -> Decimal:
        eq = self.get_equity(prices)
        if eq > self.peak_equity:
            self.peak_equity = eq
        if self.peak_equity > Decimal("0"):
            dd = (self.peak_equity - eq) / self.peak_equity * Decimal("100")
            if dd > self.max_drawdown_pct:
                self.max_drawdown_pct = dd
            if dd >= Decimal("30.0"):  # 30% Circuit Breaker
                self.trading_halted = True
        return eq

    def _fill_market(self, mark: Decimal, direction: int) -> Decimal:
        """Calculates executed price with adverse slippage for market orders."""
        return mark * (Decimal("1") + self.slippage * direction)

    def _reduce(
        self,
        sym: str,
        qty: Decimal,
        execution_price: Decimal,
        timestamp: int,
        reason: str,
        is_maker: bool = False,
    ) -> None:
        """
        Reduces or closes open position with penny-perfect accounting.
        Supports Maker limit exit (0 slippage + maker fee) for Stage 2 TP.
        """
        pos = self.open_positions[sym]
        fee_rate = self.maker_fee if is_maker else self.taker_fee
        price = execution_price if is_maker else self._fill_market(execution_price, -pos.direction)
        
        cash = qty * (price - pos.entry_price) * pos.direction - qty * price * fee_rate
        self.realized_equity += cash

        if qty < pos.current_qty:
            pos.current_qty -= qty
            pos.realized_partial += cash
            pos.partial_taken = True
            if sym in self.trackers:
                self.trackers[sym].is_tp_filled = True
                self.trackers[sym].stage_name = "Stage 2: Partial TP 60% Secured"
                self.fsm_stage_stats["STAGE2"] += 1
            self.events.append(
                dict(
                    kind="PARTIAL_TP",
                    symbol=sym,
                    qty=str(qty),
                    price=str(price),
                    fee_rate=str(fee_rate),
                    is_maker=is_maker,
                    reason=reason,
                )
            )
            return

        net = cash + pos.realized_partial + pos.funding_pnl - pos.entry_fees
        self.closed_trades.append(
            ClosedTrade(
                symbol=sym,
                direction=pos.direction,
                entry_time=pos.entry_time,
                exit_time=timestamp,
                entry_price=pos.original_entry or pos.entry_price,
                exit_price=price,
                initial_qty=pos.initial_qty,
                final_qty=qty,
                net_pnl=net,
                exit_reason=reason,
                was_pyramided=pos.is_pyramided,
                hold_hours=(timestamp - pos.entry_time) / 3600000.0,
            )
        )
        # Register re-entry cooldown
        key = f"{sym}_{pos.direction}"
        self.cooldown_tracker[key] = self.bar_counter + self.cooldown_bars

        # Clean up trackers
        self.exit_bridge.deregister_position(sym)
        self.trackers.pop(sym, None)
        del self.open_positions[sym]
        self.events.append(dict(kind="CLOSE", symbol=sym, net_pnl=str(net), reason=reason, is_maker=is_maker))

    def _manage_position(
        self,
        sym: str,
        timestamp: int,
        high: Decimal,
        low: Decimal,
        close: Decimal,
        atr: Decimal,
        execution_price: Decimal,
        prev_high: Decimal,
        prev_low: Decimal,
        highest_20: Decimal,
        lowest_20: Decimal,
        bar_open: Optional[Decimal] = None,
        z_ofi: float = 0.0,
        shock_prob: float = 0.0,
    ) -> None:
        """
        Evaluates bar against active position using NewDragon AdaptiveExitFSM.
        """
        pos = self.open_positions[sym]
        tracker_item = self.trackers.get(sym)
        if not tracker_item:
            return

        # 1. Physical Stop-Loss Verification
        hit_stop = low <= pos.current_stop if pos.direction == 1 else high >= pos.current_stop
        if hit_stop:
            fill_px = pos.current_stop
            if bar_open is not None:
                fill_px = min(bar_open, pos.current_stop) if pos.direction == 1 else max(bar_open, pos.current_stop)
            self._reduce(sym, pos.current_qty, fill_px, timestamp, "STOP_LOSS", is_maker=False)
            return

        # 2. Stage 2 Maker Partial Take-Profit Limit Execution Check
        # If order was resting at target_tp_maker and price hit target
        if not tracker_item.is_tp_filled and tracker_item.target_tp_maker > Decimal("0"):
            tp_hit = high >= tracker_item.target_tp_maker if pos.direction == 1 else low <= tracker_item.target_tp_maker
            if tp_hit:
                # 60% partial close via Maker limit order
                rules = self.instrument_rules.get(sym, {})
                step = rules.get("qtyStep", Decimal("0.001"))
                minimum = rules.get("minOrderQty", Decimal("0.001"))
                raw_close_qty = pos.initial_qty * Decimal(str(STAGE2_CLOSE_PCT))
                close_qty = (raw_close_qty / step).to_integral_value(rounding=ROUND_FLOOR) * step
                
                if close_qty >= minimum and (pos.current_qty - close_qty) >= minimum:
                    self._reduce(
                        sym=sym,
                        qty=close_qty,
                        execution_price=tracker_item.target_tp_maker,
                        timestamp=timestamp,
                        reason="STAGE2_MAKER_TP_60",
                        is_maker=True,
                    )
                    # Guaranteed profit lock stop (+0.35R)
                    initial_1r = Decimal(str(tracker_item.tracker.initial_1r_dist))
                    new_stop = pos.entry_price + pos.direction * (Decimal(str(STAGE2_LOCK_R)) * initial_1r)
                    if (new_stop - pos.current_stop) * pos.direction > 0:
                        pos.current_stop = new_stop
                    tracker_item.tracker.stage = 2
                    return

        # 3. NewDragon Adaptive Exit Bridge Evaluation
        action: ExitAction = self.exit_bridge.on_bar_update(
            symbol=sym,
            high=float(high),
            low=float(low),
            close=float(close),
            prev_high=float(prev_high),
            prev_low=float(prev_low),
            highest_20=float(highest_20),
            lowest_20=float(lowest_20),
            atr_1h=float(atr),
            z_ofi=z_ofi,
            shock_prob=shock_prob,
        )

        if action.action_type == "FULL_CLOSE_SCRATCH":
            self._reduce(sym, pos.current_qty, close, timestamp, "STAGE0_FAST_SCRATCH", is_maker=False)
            self.fsm_stage_stats["STAGE0"] += 1
            return

        elif action.action_type == "UPDATE_STOP" and action.new_stop is not None:
            cand_stop = Decimal(str(round(action.new_stop, 6)))
            if (cand_stop - pos.current_stop) * pos.direction > 0:
                pos.current_stop = cand_stop
                if "stage1" in action.reason:
                    tracker_item.stage_name = "Stage 1: Ratchet (-0.35R Stop)"
                    self.fsm_stage_stats["STAGE1"] += 1
                elif "stage3" in action.reason:
                    tracker_item.stage_name = "Stage 3: Chandelier Runner Trail"
                    self.fsm_stage_stats["STAGE3"] += 1
                self.events.append(
                    dict(kind="STOP_UPDATE", symbol=sym, new_stop=str(pos.current_stop), reason=action.reason)
                )

        # Update position high/low watermarks
        pos.highest_price = max(pos.highest_price, high)
        pos.lowest_price = min(pos.lowest_price, low)

    def process_bar_event(
        self,
        timestamp: int,
        bars: Dict[str, Dict[str, Any]],
        next_open_prices: Dict[str, Decimal],
        manage_positions: bool = True,
        btc_bullish: bool = True,
        btc_bearish: bool = True,
        blocked_symbols: Optional[Any] = None,
        z_ofi_map: Optional[Dict[str, float]] = None,
        shock_prob_map: Optional[Dict[str, float]] = None,
    ) -> None:
        """Processes closed 1h bars with Point-in-Time execution."""
        if timestamp <= self.last_bar_timestamp:
            return
        self.last_bar_timestamp = timestamp
        self.bar_counter += 1

        z_ofi_map = z_ofi_map or {}
        shock_prob_map = shock_prob_map or {}

        current = {s: Decimal(str(b["close"])) for s, b in bars.items()}
        self._observe(current)

        # 1. Evaluate open positions against closed bar
        if manage_positions:
            for sym in list(self.open_positions):
                if sym not in bars:
                    continue
                b = bars[sym]
                if not all(np.isfinite(b[k]) and b[k] > 0 for k in ("high", "low", "close", "atr")):
                    continue
                self.open_positions[sym].bars_held += 1
                opening = Decimal(str(b.get("open", b["close"])))
                prev_high = Decimal(str(b.get("prev_high", b["high"])))
                prev_low = Decimal(str(b.get("prev_low", b["low"])))
                highest_20 = Decimal(str(b.get("highest_20", b["high"])))
                lowest_20 = Decimal(str(b.get("lowest_20", b["low"])))

                self._manage_position(
                    sym=sym,
                    timestamp=timestamp + self.bar_interval_ms,
                    high=Decimal(str(b["high"])),
                    low=Decimal(str(b["low"])),
                    close=current[sym],
                    atr=Decimal(str(b["atr"])),
                    execution_price=next_open_prices.get(sym, current[sym]),
                    prev_high=prev_high,
                    prev_low=prev_low,
                    highest_20=highest_20,
                    lowest_20=lowest_20,
                    bar_open=opening,
                    z_ofi=z_ofi_map.get(sym, 0.0),
                    shock_prob=shock_prob_map.get(sym, 0.0),
                )

        self._observe(current)
        if self.trading_halted:
            for sym in list(self.open_positions):
                if sym in next_open_prices:
                    self._reduce(
                        sym,
                        self.open_positions[sym].current_qty,
                        next_open_prices[sym],
                        timestamp + self.bar_interval_ms,
                        "DRAWDOWN_HALT",
                    )
            return

        # 2. Donchian Breakout Scanner Evaluation
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

        # Convert cooldown tracker
        cooldown_tuples = {}
        for k, exp_idx in self.cooldown_tracker.items():
            parts = k.split("_")
            cooldown_tuples[(parts[0], int(parts[1]))] = exp_idx

        ranked = self.scanner.rank_candidates(
            candidates=candidates,
            currently_held_symbols=list(self.open_positions),
            current_long_count=sum(p.direction == 1 for p in self.open_positions.values()),
            current_short_count=sum(p.direction == -1 for p in self.open_positions.values()),
            btc_bullish=btc_bullish,
            btc_bearish=btc_bearish,
            cooldown_tracker=cooldown_tuples,
            current_bar_idx=self.bar_counter,
        )

        # 3. NewDragon True 1R Entry Evaluation
        for cand in ranked:
            if blocked_symbols and cand.symbol in blocked_symbols:
                continue
            rules = self.instrument_rules.get(cand.symbol)
            if not rules or cand.symbol not in next_open_prices:
                continue
            mark = next_open_prices[cand.symbol]
            if not mark.is_finite() or mark <= 0:
                continue

            entry_price = self._fill_market(mark, cand.direction)
            # Initial stop at 2.0x ATR
            stop_price = entry_price - cand.direction * cand.atr * Decimal("2.0")
            if stop_price <= 0:
                continue

            # Compute True 1R sizing via NewDragon UniversalRiskBridge
            capital = float(self.get_equity(current))
            avail_margin = float(self.get_available_margin(current))
            dd_pct = float(self.max_drawdown_pct) / 100.0
            regime = "bull" if btc_bullish else ("bear" if btc_bearish else "sideways")

            qty_float, rejection = self.risk_bridge.size_position_for_torg_bot(
                symbol=cand.symbol,
                side="LONG" if cand.direction == 1 else "SHORT",
                entry_price=float(entry_price),
                stop_price=float(stop_price),
                capital_usdt=capital,
                available_margin_usdt=avail_margin,
                current_drawdown_pct=dd_pct,
                market_regime=regime,
                qty_step=float(rules["qtyStep"]),
                min_order_qty=float(rules["minOrderQty"]),
                min_notional=float(rules.get("minNotional", Decimal("5.0"))),
                max_leverage=float(self.max_leverage),
                atr_1h=float(cand.atr),
                composite_score=1.0,
            )

            if qty_float <= 0.0 or rejection is not None:
                logger.debug(f"[REJECT_SIZING] {cand.symbol}: {rejection}")
                continue

            qty = Decimal(str(qty_float))
            # Capacity check
            notional = qty * entry_price
            if notional > Decimal(str(avail_margin * float(self.max_leverage))):
                continue

            # Open Position
            fee = qty * entry_price * self.taker_fee
            self.realized_equity -= fee

            active_pos = ActivePosition(
                symbol=cand.symbol,
                direction=cand.direction,
                initial_qty=qty,
                current_qty=qty,
                entry_price=entry_price,
                current_stop=stop_price,
                initial_atr=cand.atr,
                highest_price=entry_price,
                lowest_price=entry_price,
                entry_time=timestamp + self.bar_interval_ms,
                original_entry=entry_price,
                entry_fees=fee,
                bars_held=0,
                breakout_level=entry_price,
            )
            self.open_positions[cand.symbol] = active_pos

            # Register in NewDragon AdaptiveExitBridge
            nd_tracker = self.exit_bridge.register_position(
                symbol=cand.symbol,
                side="LONG" if cand.direction == 1 else "SHORT",
                entry_price=float(entry_price),
                stop_price=float(stop_price),
                qty=float(qty),
            )

            # Pre-calculate Stage 2 Maker Take-Profit Limit Price (+1.40R)
            initial_1r = abs(entry_price - stop_price)
            target_tp = entry_price + cand.direction * (Decimal(str(STAGE2_R)) * initial_1r)

            self.trackers[cand.symbol] = PositionTrackerV38(
                position=active_pos,
                tracker=nd_tracker,
                target_tp_maker=target_tp,
                is_tp_filled=False,
                stage_name="Stage 0: Initial (-1.0R Stop)",
            )

            self.events.append(
                dict(
                    kind="OPEN",
                    symbol=cand.symbol,
                    qty=str(qty),
                    price=str(entry_price),
                    stop=str(stop_price),
                    tp_maker=str(target_tp),
                )
            )
            break

        eq = self._observe({**current, **next_open_prices})
        self.equity_history.append((timestamp + self.bar_interval_ms, eq))
        self.equity_history = self.equity_history[-10000:]
