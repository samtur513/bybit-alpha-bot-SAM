"""
v3.9 Scalp Order Management System (OMS) with NewDragon Micro-Router and Z_OFI Scratch.
"""

from collections import deque
import hashlib
import hmac
import logging
import math
import time
from typing import Dict, List, Optional, Tuple
import uuid

try:
    import orjson
except ImportError:
    import json
    class _OrjsonFallback:
        @staticmethod
        def dumps(obj, *args, **kwargs) -> bytes:
            return json.dumps(obj).encode("utf-8")
        @staticmethod
        def loads(obj, *args, **kwargs):
            if isinstance(obj, (bytes, bytearray)):
                return json.loads(obj.decode("utf-8"))
            return json.loads(obj)
    orjson = _OrjsonFallback()

from v3_7.src.scalp_oms import ScalpOMS, PendingLimitOrder, ActivePosition
from v3_7.src.scalp_engines import SignalIntent
from v3_9.src.config import ScalperConfigV39
from v3_9.src.microstructure_v39 import SymbolMicrostructureStateV39

logger = logging.getLogger("ScalpOMSV39")


class ScalpOMSV39(ScalpOMS):
    """
    Enhanced Scalp OMS integrating:
    1. NewDragon v4 Micro-Account Router (strict minNotional enforcement).
    2. Z_OFI Order Flow Imbalance Fast Scratch.
    """

    def __init__(self, cfg: ScalperConfigV39, states: Optional[Dict[str, SymbolMicrostructureStateV39]] = None):
        super().__init__(cfg, states or {})
        self.cfg: ScalperConfigV39 = cfg
        self.states: Dict[str, SymbolMicrostructureStateV39] = states or {}
        self.scratch_count: int = 0

    def calculate_safe_qty(self, intent: SignalIntent, state: SymbolMicrostructureStateV39) -> float:
        """
        NewDragon Micro-Router:
        Calculates position quantity with strict minNotional ($5 alts / $100 BTC) protection.
        Never expands risk to meet exchange minimums on small accounts.
        """
        price_risk_pct = abs(intent.entry_price - intent.sl_price) / max(intent.entry_price, 1e-9)
        total_risk_pct = price_risk_pct + self.cfg.roundtrip_mt
        notional_by_risk = (self.cfg.equity_usdt * self.cfg.risk_per_trade_pct) / max(total_risk_pct, 0.0015)

        max_book_notional = self.cfg.max_book_depth_pct * state.top3_book_notional_usdt(intent.side)
        dollar_cap = getattr(self.cfg, 'max_loss_usdt_cap', 1.50)
        max_by_dollar_cap = dollar_cap / max(total_risk_pct, 0.0015)
        max_single_notional = getattr(self.cfg, 'max_single_trade_notional', 100.0)
        target_notional = min(notional_by_risk, max_by_dollar_cap, max_book_notional, max_single_notional, self.cfg.equity_usdt * self.cfg.max_leverage_cap)

        raw_qty = target_notional / max(intent.entry_price, 1e-9)
        step = state.meta.qty_step
        qty = math.floor(raw_qty / step) * step if step > 0 else raw_qty
        qty = round(qty, 6)

        # Micro-Account minNotional validation
        min_notional = self.cfg.min_notional_btc if intent.symbol == "BTCUSDT" else self.cfg.min_notional_alts
        notional = qty * intent.entry_price

        if qty < state.meta.min_qty or notional < min_notional:
            logger.debug(
                f"[MICRO_ROUTER_REJECT] {intent.symbol}: notional=${notional:.2f} < minNotional=${min_notional:.2f}"
            )
            return 0.0

        return qty

    async def monitor_step(self, now_ms: int) -> None:
        """
        25ms evaluation step with Z_OFI Fast Scratch.
        """
        btc_state = self.states.get(self.cfg.leader_symbol)

        # 1. Order chasing and expiration
        for sym, pending in list(self.pending_orders.items()):
            st = self.states.get(sym)
            if not st or st.mid_price <= 0:
                continue

            age_ms = now_ms - pending.placed_ms
            if pending.side == "Buy":
                price_ran_away = st.best_bid > pending.price + 2.0 * st.meta.tick_size
            else:
                price_ran_away = st.best_ask < pending.price - 2.0 * st.meta.tick_size

            if price_ran_away and age_ms >= 200:
                if pending.amend_count < 3:
                    pending.amend_count += 1
                    if pending.side == "Buy":
                        new_p = st.best_bid + st.meta.tick_size if st.spread_ticks >= 2.0 else st.best_bid
                        q_vol = 0.0 if st.spread_ticks >= 2.0 else (st.sorted_bids[0][1] if st.sorted_bids else 0.0)
                    else:
                        new_p = st.best_ask - st.meta.tick_size if st.spread_ticks >= 2.0 else st.best_ask
                        q_vol = 0.0 if st.spread_ticks >= 2.0 else (st.sorted_asks[0][1] if st.sorted_asks else 0.0)

                    pending.price = new_p
                    pending.placed_ms = now_ms
                    pending.queue_ahead_vol = q_vol
                else:
                    self.pending_orders.pop(sym, None)
                    logger.info(f"[ORDER_EXPIRED] {sym} cancelled (chase limit reached)")

            elif age_ms >= 3500:
                self.pending_orders.pop(sym, None)

        # 2. Active positions monitoring
        for sym, pos in list(self.active_positions.items()):
            st = self.states.get(sym)
            if not st or st.mid_price <= 0:
                continue

            hold_sec = (now_ms - pos.opened_ms) / 1000.0
            curr_p = st.mid_price
            is_long = pos.side == "Buy"

            # A. Stop Loss
            hit_sl = curr_p <= pos.sl_price if is_long else curr_p >= pos.sl_price
            if hit_sl:
                await self._close_position(pos, curr_p, is_maker=False, reason="STOP_LOSS")
                continue

            # B. Leader Reversal Stop for E3
            if pos.engine_id == "E3_LEADLAG" and btc_state and pos.leader_entry_mid > 0:
                btc_curr_ret = math.log(btc_state.mid_price / pos.leader_entry_mid)
                if is_long and btc_curr_ret < -self.cfg.e3_leader_reversal_threshold * pos.leader_ref_ret:
                    await self._close_position(pos, curr_p, is_maker=False, reason="BTC_REVERSAL_STOP")
                    continue
                elif not is_long and btc_curr_ret > self.cfg.e3_leader_reversal_threshold * abs(pos.leader_ref_ret):
                    await self._close_position(pos, curr_p, is_maker=False, reason="BTC_REVERSAL_STOP")
                    continue

            # C. NewDragon Z_OFI Order Flow Imbalance Fast Scratch (2-Stage Auditor Protocol)
            if self.cfg.enable_z_ofi_scratch and hold_sec <= self.cfg.z_ofi_window_sec and not pos.tp1_filled:
                z_ofi = getattr(st, "get_z_ofi", lambda: 0.0)()
                is_adverse_flow = (is_long and z_ofi < -self.cfg.z_ofi_scratch_threshold) or (
                    not is_long and z_ofi > self.cfg.z_ofi_scratch_threshold
                )
                if is_adverse_flow:
                    critical_flow = (is_long and z_ofi <= -2.5) or (not is_long and z_ofi >= 2.5)
                    elapsed_scratch_ms = (now_ms - getattr(pos, "scratch_start_ms", now_ms)) if getattr(pos, "scratch_stage", 0) == 1 else 0

                    if critical_flow or elapsed_scratch_ms >= 500:
                        # Stage 2 / Critical flow: экстренный выход
                        self.scratch_count += 1
                        logger.warning(
                            f"[OFI_FAST_SCRATCH] {sym} {pos.side} экстренный сброс в рынок! "
                            f"Hold={hold_sec:.1f}s | Z_OFI={z_ofi:.2f}"
                        )
                        await self._close_position(pos, curr_p, is_maker=False, reason="OFI_FAST_SCRATCH")
                        continue
                    elif getattr(pos, "scratch_stage", 0) == 0:
                        # Stage 1: умеренный разворот (2.0 <= |Z_OFI| < 2.5), ставим мейкер-выход
                        pos.scratch_stage = 1
                        pos.scratch_start_ms = now_ms
                        self.scratch_count += 1
                        logger.info(
                            f"[OFI_SCRATCH_STAGE1] {sym} {pos.side} разворот институционального потока: "
                            f"Hold={hold_sec:.1f}s | Z_OFI={z_ofi:.2f}. Выставление мейкер-выхода."
                        )
                        true_be = pos.entry_price * (1.0 + self.cfg.roundtrip_mm) if is_long else pos.entry_price * (1.0 - self.cfg.roundtrip_mm)
                        if is_long and st.best_ask >= true_be:
                            pos.tp1_price = st.best_ask
                        elif not is_long and st.best_bid <= true_be:
                            pos.tp1_price = st.best_bid

            # D0. Dynamic Breakeven (Early Profit Lock)
            be_trig = getattr(self.cfg, "dynamic_breakeven_pct", 0.0035)
            cur_pnl_pct = (curr_p - pos.entry_price) / pos.entry_price if is_long else (pos.entry_price - curr_p) / pos.entry_price
            if cur_pnl_pct >= be_trig and not getattr(pos, "is_breakeven_set", False):
                be_sl = pos.entry_price * (1.0 + 0.0005) if is_long else pos.entry_price * (1.0 - 0.0005)
                if (is_long and be_sl > pos.sl_price) or (not is_long and be_sl < pos.sl_price):
                    pos.sl_price = be_sl
                    pos.is_breakeven_set = True
                    logger.info(f"[DYNAMIC BREAKEVEN] {sym} PnL={cur_pnl_pct*100:.2f}%. Стоп перенесен в безубыток {be_sl:.5f}")

            # D. TP1: 50% partial take profit via Maker
            hit_tp1 = curr_p >= pos.tp1_price if is_long else curr_p <= pos.tp1_price
            if not pos.tp1_filled and hit_tp1:
                close_qty = pos.qty * 0.5
                step = st.meta.qty_step
                realized_slice = math.floor(close_qty / step) * step if step > 0 else close_qty
                if realized_slice > 0:
                    exit_fee = self.cfg.fee_maker
                    total_fee = self.cfg.fee_maker + exit_fee
                    slice_gross_pct = (pos.tp1_price - pos.entry_price) / pos.entry_price if is_long else (pos.entry_price - pos.tp1_price) / pos.entry_price
                    slice_net_pct = slice_gross_pct - total_fee
                    slice_net_usdt = realized_slice * pos.entry_price * slice_net_pct

                    pos.qty -= realized_slice
                    pos.tp1_filled = True
                    pos.tp1_realized_pnl_usdt += slice_net_usdt
                    self.daily_pnl_usdt += slice_net_usdt

                    # Move remaining stop to breakeven (+0.10%)
                    if is_long:
                        pos.sl_price = pos.entry_price * (1.0 + max(0.0010, self.cfg.roundtrip_mm * 1.2))
                    else:
                        pos.sl_price = pos.entry_price * (1.0 - max(0.0010, self.cfg.roundtrip_mm * 1.2))

                    logger.info(
                        f"[TP1_MAKER] {sym} {pos.side} Qty={realized_slice} NetPnL=+${slice_net_usdt:.4f} | "
                        f"SL moved to BE ({pos.sl_price:.5f})"
                    )

            # E. TP2: Runner close
            hit_tp2 = curr_p >= pos.tp2_price if is_long else curr_p <= pos.tp2_price
            if pos.tp1_filled and hit_tp2:
                await self._close_position(pos, curr_p, is_maker=True, reason="TP2_RUNNER_MAKER")
                continue

            # F. Time Stop
            if hold_sec >= pos.max_hold_sec:
                await self._close_position(pos, curr_p, is_maker=False, reason="TIME_STOP_EXPIRED")
                continue

    def get_status(self) -> dict:
        """Telemetry status dict including Z_OFI and Micro-Router metrics."""
        base_status = super().get_status()
        base_status["version"] = self.cfg.VERSION if hasattr(self.cfg, "VERSION") else "3.9.0"
        base_status["version_name"] = "v3.9 NewDragon Scalp Lab"
        base_status["port"] = 8092
        base_status["scratch_count"] = self.scratch_count
        base_status["active_positions_count"] = len(self.active_positions)
        base_status["pending_orders_count"] = len(self.pending_orders)
        base_status["closed_trades_count"] = len(self.closed_trades_history)

        # Include live microstructure radar per monitored symbol
        radar_map = {}
        z_ofi_map = {}
        for sym, st in self.states.items():
            z = round(st.get_z_ofi(), 2) if hasattr(st, "get_z_ofi") else 0.0
            z_ofi_map[sym] = z
            radar_map[sym] = {
                "mid_price": getattr(st, "mid_price", 0.0),
                "spread_ticks": round(getattr(st, "spread_ticks", 0.0), 1),
                "obi": round(getattr(st, "obi", 0.0), 3),
                "z_ofi": z,
            }
        base_status["radar"] = radar_map
        base_status["z_ofi_radar"] = z_ofi_map
        return base_status
