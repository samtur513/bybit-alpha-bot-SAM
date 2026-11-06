"""
Scalp Order Management System (OMS) v3.7-v4-exp:
Integrates NewDragon v4 UniversalRisk minNotional Guard and 2-Stage Z_OFI Fast Scratch.
"""

from collections import deque
import logging
import math
from pathlib import Path
import sys
import time
from typing import Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VENDOR_ND4 = REPO_ROOT / "vendor" / "newdragon_v4" / "NewDragon_v4_Universal"
if str(VENDOR_ND4) not in sys.path:
    sys.path.insert(0, str(VENDOR_ND4))

from adapters.torg_bot_3_bridge import UniversalRiskBridge
from v3_7.src.scalp_oms import ScalpOMS, PendingLimitOrder, ActivePosition
from v3_7.src.scalp_engines import SignalIntent
from v3_7_v4_exp.src.config import ScalperConfigV37V4
from v3_7_v4_exp.src.microstructure_v4 import SymbolMicrostructureStateV4

logger = logging.getLogger("ScalpOMSV37V4")


class ScalpOMSV4(ScalpOMS):
    """
    Enhanced Scalp OMS integrating:
    1. NewDragon v4 UniversalRisk minNotional Guard.
    2. 2-Stage Z_OFI Order Flow Imbalance Fast Scratch (15s Window).
    3. Dynamic Breakeven Ratchet (+0.35% -> BE+0.05%).
    """

    def __init__(self, cfg: ScalperConfigV37V4, states: Optional[Dict[str, SymbolMicrostructureStateV4]] = None):
        super().__init__(cfg, states or {})
        self.cfg: ScalperConfigV37V4 = cfg
        self.states: Dict[str, SymbolMicrostructureStateV4] = states or {}
        self.scratch_count: int = 0
        self.risk_bridge = UniversalRiskBridge(
            taker_fee=cfg.fee_taker,
            maker_fee=cfg.fee_maker,
            max_leverage=cfg.max_leverage_cap,
        )

    def calculate_safe_qty(self, intent: SignalIntent, state: SymbolMicrostructureStateV4) -> float:
        """
        Calculates position quantity using NewDragon UniversalRiskBridge with minNotional protection.
        Rejects orders if target size is below exchange minNotional ($5 alts / $100 BTC).
        """
        min_notional = self.cfg.min_notional_btc if intent.symbol == "BTCUSDT" else self.cfg.min_notional_alts

        # Use NewDragon UniversalRiskBridge
        qty, reason = self.risk_bridge.size_position_for_torg_bot(
            symbol=intent.symbol,
            side="LONG" if intent.side == "Buy" else "SHORT",
            entry_price=intent.entry_price,
            stop_price=intent.sl_price,
            capital_usdt=self.cfg.equity_usdt,
            available_margin_usdt=self.cfg.equity_usdt,
            current_drawdown_pct=0.01,
            qty_step=state.meta.qty_step,
            min_order_qty=state.meta.min_qty,
            min_notional=min_notional,
            max_leverage=self.cfg.max_leverage_cap,
            active_correlated_positions=len(self.active_positions),
            max_correlated_positions=self.cfg.max_concurrent_positions,
        )

        if qty <= 0.0 or reason is not None:
            logger.debug(f"[UNIVERSAL_RISK_REJECT] {intent.symbol}: {reason}")
            return 0.0

        # Additional hard dollar loss cap ($1.50) and book depth constraint
        price_risk_pct = abs(intent.entry_price - intent.sl_price) / max(intent.entry_price, 1e-9)
        total_risk_pct = price_risk_pct + self.cfg.roundtrip_mt
        max_by_dollar_cap = self.cfg.max_loss_usdt_cap / max(total_risk_pct, 0.0015)
        max_book_notional = self.cfg.max_book_depth_pct * state.top3_book_notional_usdt(intent.side)
        target_notional = min(qty * intent.entry_price, max_by_dollar_cap, max_book_notional, self.cfg.max_single_trade_notional)

        raw_qty = target_notional / max(intent.entry_price, 1e-9)
        step = state.meta.qty_step
        final_qty = math.floor(raw_qty / step) * step if step > 0 else raw_qty
        final_qty = round(final_qty, 6)

        notional = final_qty * intent.entry_price
        if final_qty < state.meta.min_qty or notional < min_notional:
            logger.debug(
                f"[MIN_NOTIONAL_GUARD] {intent.symbol}: notional=${notional:.2f} < minNotional=${min_notional:.2f}"
            )
            return 0.0

        return final_qty

    async def monitor_step(self, now_ms: int) -> None:
        """
        25ms evaluation step with 2-Stage Z_OFI Fast Scratch and Dynamic Breakeven.
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

            # C. NewDragon 2-Stage Z_OFI Fast Scratch
            if self.cfg.enable_z_ofi_scratch and hold_sec <= self.cfg.z_ofi_window_sec and not pos.tp1_filled:
                z_ofi = getattr(st, "get_z_ofi", lambda: 0.0)()
                is_adverse_flow = (is_long and z_ofi < -self.cfg.z_ofi_scratch_threshold) or (
                    not is_long and z_ofi > self.cfg.z_ofi_scratch_threshold
                )
                if is_adverse_flow:
                    critical_flow = (is_long and z_ofi <= -self.cfg.z_ofi_critical_threshold) or (
                        not is_long and z_ofi >= self.cfg.z_ofi_critical_threshold
                    )
                    elapsed_scratch_ms = (now_ms - getattr(pos, "scratch_start_ms", now_ms)) if getattr(pos, "scratch_stage", 0) == 1 else 0

                    if critical_flow or elapsed_scratch_ms >= 500:
                        # Stage 2 / Critical flow: emergency market dump
                        self.scratch_count += 1
                        logger.warning(
                            f"[OFI_FAST_SCRATCH] {sym} {pos.side} экстренный сброс в рынок! "
                            f"Hold={hold_sec:.1f}s | Z_OFI={z_ofi:.2f}"
                        )
                        await self._close_position(pos, curr_p, is_maker=False, reason="OFI_FAST_SCRATCH")
                        continue
                    elif getattr(pos, "scratch_stage", 0) == 0:
                        # Stage 1: Post-Only Maker scratch at BE + maker fees
                        pos.scratch_stage = 1
                        pos.scratch_start_ms = now_ms
                        self.scratch_count += 1
                        logger.info(
                            f"[OFI_SCRATCH_STAGE1] {sym} {pos.side} разворот потока Z_OFI={z_ofi:.2f}. "
                            f"Выставление мейкер-выхода в безубыток."
                        )
                        true_be = pos.entry_price * (1.0 + self.cfg.roundtrip_mm) if is_long else pos.entry_price * (1.0 - self.cfg.roundtrip_mm)
                        if is_long and st.best_ask >= true_be:
                            pos.tp1_price = st.best_ask
                        elif not is_long and st.best_bid <= true_be:
                            pos.tp1_price = st.best_bid

            # D. Dynamic Breakeven Ratchet (+0.35% -> BE+0.05%)
            cur_pnl_pct = (curr_p - pos.entry_price) / pos.entry_price if is_long else (pos.entry_price - curr_p) / pos.entry_price
            if cur_pnl_pct >= self.cfg.dynamic_breakeven_pct and not getattr(pos, "is_breakeven_set", False):
                be_sl = pos.entry_price * (1.0 + 0.0005) if is_long else pos.entry_price * (1.0 - 0.0005)
                if (is_long and be_sl > pos.sl_price) or (not is_long and be_sl < pos.sl_price):
                    pos.sl_price = be_sl
                    pos.is_breakeven_set = True
                    logger.info(f"[DYNAMIC BREAKEVEN] {sym} PnL={cur_pnl_pct*100:.2f}%. Стоп перенесен в безубыток {be_sl:.5f}")

            # E. TP1: 50% partial take profit via Maker
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
                    slice_net_usdt = slice_net_pct * (realized_slice * pos.entry_price)

                    self.realized_pnl += slice_net_usdt
                    pos.qty -= realized_slice
                    pos.tp1_filled = True
                    pos.sl_price = pos.entry_price  # Move SL to breakeven for remaining runner
                    logger.info(f"[TP1_MAKER_HIT] {sym} {pos.side} 50% зафиксировано. Net PnL: +${slice_net_usdt:.2f}. Остаток переведен в безубыток.")

            # F. TP2 / Runner Exit
            hit_tp2 = curr_p >= pos.tp2_price if is_long else curr_p <= pos.tp2_price
            if hit_tp2:
                await self._close_position(pos, curr_p, is_maker=True, reason="TP2_RUNNER")
                continue

            # G. Max Hold Time Exit
            if hold_sec >= pos.max_hold_sec:
                await self._close_position(pos, curr_p, is_maker=False, reason="MAX_HOLD_TIMEOUT")
                continue
