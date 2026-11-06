"""
v3.7 & v3.9 Scalp Strategy Engines (Optimized Production Freeze 2026-11-05):
1. Engine 1: OBI Breakout & Depth Depletion (Long & Short with Strict BTC Gate)
2. Engine 2: Liquidity Sweep & Absorption Reversion (Long & Short with Trend Guard)
3. Engine 3: Lead-Lag Beta-Catchup to BTC (Long & Short, High R:R Fee-Robust)
"""

from dataclasses import dataclass
from typing import Optional

from v3_7.src.config import ScalperConfig
from v3_7.src.microstructure import SymbolMicrostructureState


@dataclass
class SignalIntent:
    engine_id: str          # "E2_SWEEP" | "E3_LEADLAG" | "E1_OBI"
    priority: int           # 1 (highest) .. 3
    symbol: str
    side: str               # "Buy" | "Sell"
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    max_hold_sec: float
    leader_ref_ret: float = 0.0
    leader_entry_mid: float = 0.0
    reason: str = ""


class ThreeEnginesEvaluate:
    def __init__(self, cfg: ScalperConfig):
        self.cfg = cfg

    # =========================================================================
    # ДВИЖОК 1: OBI + FLOW TOXICITY BREAKOUT (LONG & SHORT С BTC ФИЛЬТРОМ)
    # =========================================================================
    def evaluate_engine1_obi_breakout(
        self, state: SymbolMicrostructureState, now_ms: int, btc_state: Optional[SymbolMicrostructureState] = None
    ) -> Optional[SignalIntent]:
        m = state.meta
        if state.mid_price <= 0 or state.spread_ticks > self.cfg.e1_max_spread_ticks:
            return None

        # Строгий макро-фильтр BTC (Strict Macro Trend Gate)
        # Strict Macro & Micro Trend Gate BTC (Prevents shorting into V-bounces or longing into waterfall dumps)
        btc_bull = True
        btc_bear = True
        if btc_state and btc_state.mid_price > 0:
            bm = btc_state.meta
            vwap = getattr(bm, "vwap_session", 0.0) or 0.0
            ema20 = getattr(bm, "ema20_15m", 0.0) or 0.0
            ema9_5m = getattr(bm, "ema9_5m", 0.0) or 0.0

            if vwap > 0 and ema20 > 0:
                btc_bull = (btc_state.mid_price >= vwap) and (btc_state.mid_price >= ema20)
                btc_bear = (btc_state.mid_price <= vwap) and (btc_state.mid_price <= ema20)
            elif vwap > 0:
                btc_bull = btc_state.mid_price >= vwap
                btc_bear = btc_state.mid_price <= vwap

            # 5m EMA9 alignment check
            if ema9_5m > 0:
                btc_bull = btc_bull and (btc_state.mid_price >= ema9_5m)
                btc_bear = btc_bear and (btc_state.mid_price <= ema9_5m)

            # Micro-momentum veto (60s rolling window):
            # If BTC is actively rebounding (+0.04% in 60s), VETO all shorts!
            # If BTC is actively waterfall dumping (-0.04% in 60s), VETO all longs!
            btc_ret_60s, _, _ = btc_state.get_depletion_and_return(now_ms, 60000)
            if btc_ret_60s > 0.0004:
                btc_bear = False
            elif btc_ret_60s < -0.0004:
                btc_bull = False

        # Фильтр эффективности Кауфмана: полная блокировка микро-стакана в боковом распиле (KER < 0.30)
        min_er = getattr(self.cfg, "e1_min_er_15m", 0.30)
        if getattr(m, "er_15m", 0.0) < min_er:
            return None

        # Фильтр минимальной волатильности (отсечение мертвых инструментов)
        min_natr = getattr(self.cfg, "e1_min_natr_5m", 0.0035)
        if getattr(m, "natr_5m", 0.0) < min_natr:
            return None

        natr = max(m.natr_5m, min_natr)
        tfi_2s, buy_2s, sell_2s, avg_buy_2s, avg_sell_2s = state.get_trade_flow_metrics(now_ms, 2000)
        _, dr_ask_400ms, dr_bid_400ms, delta_ask_vol, delta_bid_vol = state.get_depletion_with_volumes(now_ms, 400)
        _, buy_400ms, sell_400ms, _, _ = state.get_trade_flow_metrics(now_ms, 400)
        real_ask_absorption = (buy_400ms >= 0.50 * delta_ask_vol) if delta_ask_vol > 0 else True
        real_bid_absorption = (sell_400ms >= 0.50 * delta_bid_vol) if delta_bid_vol > 0 else True

        # 1. LONG УСЛОВИЯ (строго по восходящему тренду BTC и альткоина)
        long_trend_ok = (
            btc_bull
            and (state.mid_price >= m.vwap_session or state.mid_price >= m.ema9_5m)
        )
        if (
            long_trend_ok
            and state.obi >= self.cfg.e1_obi_threshold
            and tfi_2s >= self.cfg.e1_tfi_threshold
            and avg_buy_2s > 0
            and buy_2s >= self.cfg.e1_volume_surge_mult * avg_buy_2s
            and dr_ask_400ms >= self.cfg.e1_depletion_threshold
            and real_ask_absorption
            and (state.micro_price - state.mid_price >= 0.10 * m.tick_size)
        ):
            entry = state.best_bid + state.meta.tick_size if state.spread_ticks >= 2.0 else state.best_bid
            tp1 = entry * (1.0 + max(0.0085, 1.25 * natr))
            tp2 = entry * (1.0 + max(0.0180, 2.50 * natr))
            sl = entry * (1.0 - max(0.0050, 0.65 * natr))

            return SignalIntent(
                engine_id="E1_OBI",
                priority=3,
                symbol=state.symbol,
                side="Buy",
                entry_price=entry,
                sl_price=sl,
                tp1_price=tp1,
                tp2_price=tp2,
                max_hold_sec=self.cfg.e1_max_hold_sec,
                reason=f"LONG OBI={state.obi:.2f} TFI={tfi_2s:.2f} DR_ask={dr_ask_400ms:.2f}",
            )

        # 2. SHORT УСЛОВИЯ (строго по нисходящему тренду BTC и альткоина; блокировка контртренд-шортов)
        short_trend_ok = (
            btc_bear
            and (state.mid_price <= m.vwap_session or state.mid_price <= m.ema9_5m)
        )
        if (
            short_trend_ok
            and state.obi <= -self.cfg.e1_obi_threshold
            and tfi_2s <= -self.cfg.e1_tfi_threshold
            and avg_sell_2s > 0
            and sell_2s >= self.cfg.e1_volume_surge_mult * avg_sell_2s
            and dr_bid_400ms >= self.cfg.e1_depletion_threshold
            and real_bid_absorption
            and (state.mid_price - state.micro_price >= 0.10 * m.tick_size)
        ):
            entry = state.best_ask - state.meta.tick_size if state.spread_ticks >= 2.0 else state.best_ask
            tp1 = entry * (1.0 - max(0.0085, 1.25 * natr))
            tp2 = entry * (1.0 - max(0.0180, 2.50 * natr))
            sl = entry * (1.0 + max(0.0050, 0.65 * natr))

            return SignalIntent(
                engine_id="E1_OBI",
                priority=3,
                symbol=state.symbol,
                side="Sell",
                entry_price=entry,
                sl_price=sl,
                tp1_price=tp1,
                tp2_price=tp2,
                max_hold_sec=self.cfg.e1_max_hold_sec,
                reason=f"SHORT OBI={state.obi:.2f} TFI={tfi_2s:.2f} DR_bid={dr_bid_400ms:.2f}",
            )

        return None

    # =========================================================================
    # ДВИЖОК 2: LIQUIDITY SWEEP & ABSORPTION REVERSION (LONG & SHORT)
    # =========================================================================
    def evaluate_engine2_liquidity_sweep(
        self, state: SymbolMicrostructureState, btc_state: SymbolMicrostructureState, now_ms: int
    ) -> Optional[SignalIntent]:
        m = state.meta
        if m.atr_15m <= 0 or m.hurst_15m > 0.62:
            return None

        btc_ret_5s, _, _ = btc_state.get_depletion_and_return(now_ms, 5000)
        if abs(btc_ret_5s) > 0.0045:
            return None

        # ---------------------------------------------------------------------
        # 1. LONG SWEEP: Закол уровня поддержки (swing_low_1h)
        # ---------------------------------------------------------------------
        if state.sweep_low_active and m.swing_low_1h > 0:
            elapsed_sec = (now_ms - state.sweep_start_ms) / 1000.0
            if elapsed_sec > self.cfg.e2_max_sweep_time_sec:
                state.reset_sweep("long")
            else:
                depth = m.swing_low_1h - state.sweep_min_price
                valid_depth = (
                    self.cfg.e2_min_depth_pct * m.swing_low_1h
                    <= depth
                    <= self.cfg.e2_max_depth_atr_mult * m.atr_15m
                )
                reclaim = state.mid_price >= m.swing_low_1h + m.tick_size

                if valid_depth and reclaim:
                    tfi_3s, _, _, avg_buy_3s, _ = state.get_trade_flow_metrics(now_ms, 3000)
                    avg_sell_1s = max(avg_buy_3s / 3.0, 1e-6)
                    norm_sell = state.sweep_sell_vol / max(avg_sell_1s * max(elapsed_sec, 1.0), 1e-6)
                    k_abs = norm_sell / max(depth / (0.1 * m.atr_15m), 1.0)

                    if k_abs >= self.cfg.e2_k_abs_threshold and tfi_3s >= 0.35 and state.obi >= 0.20:
                        entry = round(m.swing_low_1h / m.tick_size) * m.tick_size
                        sl = state.sweep_min_price - 0.12 * m.atr_15m
                        risk_pct = (entry - sl) / entry

                        if self.cfg.e2_min_risk_pct <= risk_pct <= self.cfg.e2_max_risk_pct:
                            state.reset_sweep("long")
                            tp1 = max(entry + 2.5 * (entry - sl), entry * 1.0085)
                            tp2 = max(m.vwap_session, tp1 * 1.005)
                            return SignalIntent(
                                engine_id="E2_SWEEP",
                                priority=1,
                                symbol=state.symbol,
                                side="Buy",
                                entry_price=entry,
                                sl_price=sl,
                                tp1_price=tp1,
                                tp2_price=tp2,
                                max_hold_sec=self.cfg.e2_max_hold_sec,
                                reason=f"LONG Sweep K_abs={k_abs:.2f} Depth={depth/entry*100:.2f}%",
                            )

        # ---------------------------------------------------------------------
        # 2. SHORT SWEEP: Закол уровня сопротивления (swing_high_1h)
        # Блокировка если BTC находится в восходящем тренде
        # ---------------------------------------------------------------------
        bm = btc_state.meta
        btc_above_vwap = btc_state.mid_price >= getattr(bm, "vwap_session", 0.0)
        if state.sweep_high_active and m.swing_high_1h > 0 and not btc_above_vwap:
            elapsed_sec = (now_ms - state.sweep_high_start_ms) / 1000.0
            if elapsed_sec > self.cfg.e2_max_sweep_time_sec:
                state.reset_sweep("short")
            else:
                depth = state.sweep_max_price - m.swing_high_1h
                valid_depth = (
                    self.cfg.e2_min_depth_pct * m.swing_high_1h
                    <= depth
                    <= self.cfg.e2_max_depth_atr_mult * m.atr_15m
                )
                reclaim = state.mid_price <= m.swing_high_1h - m.tick_size

                if valid_depth and reclaim:
                    tfi_3s, _, _, _, avg_sell_3s = state.get_trade_flow_metrics(now_ms, 3000)
                    avg_buy_1s = max(avg_sell_3s / 3.0, 1e-6)
                    norm_buy = state.sweep_buy_vol / max(avg_buy_1s * max(elapsed_sec, 1.0), 1e-6)
                    k_abs = norm_buy / max(depth / (0.1 * m.atr_15m), 1.0)

                    if k_abs >= self.cfg.e2_k_abs_threshold and tfi_3s <= -0.35 and state.obi <= -0.20:
                        entry = round(m.swing_high_1h / m.tick_size) * m.tick_size
                        sl = state.sweep_max_price + 0.12 * m.atr_15m
                        risk_pct = (sl - entry) / entry

                        if self.cfg.e2_min_risk_pct <= risk_pct <= self.cfg.e2_max_risk_pct:
                            state.reset_sweep("short")
                            tp1 = min(entry - 2.5 * (sl - entry), entry * 0.9915)
                            tp2 = min(m.vwap_session, tp1 * 0.995)
                            return SignalIntent(
                                engine_id="E2_SWEEP",
                                priority=1,
                                symbol=state.symbol,
                                side="Sell",
                                entry_price=entry,
                                sl_price=sl,
                                tp1_price=tp1,
                                tp2_price=tp2,
                                max_hold_sec=self.cfg.e2_max_hold_sec,
                                reason=f"SHORT Sweep K_abs={k_abs:.2f} Depth={depth/entry*100:.2f}%",
                            )

        return None

    # =========================================================================
    # ДВИЖОК 3: LEAD-LAG BETA-CATCHUP TO BTC (LONG & SHORT) - ВЫСШИЙ ПРИОРИТЕТ
    # =========================================================================
    def evaluate_engine3_lead_lag(
        self, state: SymbolMicrostructureState, btc_state: SymbolMicrostructureState, now_ms: int
    ) -> Optional[SignalIntent]:
        m = state.meta
        if state.symbol == self.cfg.leader_symbol or btc_state.mid_price <= 0:
            return None
        if m.corr_btc < self.cfg.e3_min_corr or m.beta_btc < self.cfg.e3_min_beta:
            return None
        if state.spread_ticks > self.cfg.e3_max_spread_ticks:
            return None

        # Multi-window sampling: 2.5s, 5.0s, and 10.0s for high fidelity
        r_btc_2500, _, _ = btc_state.get_depletion_and_return(now_ms, 2500)
        r_btc_5000, _, _ = btc_state.get_depletion_and_return(now_ms, 5000)
        r_btc_10000, _, _ = btc_state.get_depletion_and_return(now_ms, 10000)

        max_r = max(abs(r_btc_2500), abs(r_btc_5000), abs(r_btc_10000))
        if max_r == abs(r_btc_2500):
            r_btc, win_ms = r_btc_2500, 2500
        elif max_r == abs(r_btc_5000):
            r_btc, win_ms = r_btc_5000, 5000
        else:
            r_btc, win_ms = r_btc_10000, 10000

        r_alt, _, _ = state.get_depletion_and_return(now_ms, win_ms)
        tfi_btc, _, _, _, _ = btc_state.get_trade_flow_metrics(now_ms, win_ms)

        # Dynamic Adaptive Impulse based on rolling BTC NATR_5m
        btc_natr = getattr(btc_state.meta, "natr_5m", 0.0025) or 0.0025
        dyn_impulse = max(self.cfg.e3_btc_impulse_ret, 0.40 * btc_natr)
        dyn_min_lag = max(self.cfg.e3_min_lag_spread, 0.60 * dyn_impulse)
        dyn_tfi = max(0.40, self.cfg.e3_btc_tfi_threshold)

        # 1. LONG LEAD-LAG (Бычий импульс BTC - строго подтвержденный VWAP и EMA20)
        bm = btc_state.meta
        ema20_val = getattr(bm, "ema20_15m", 0.0) or 0.0
        ema9_5m = getattr(bm, "ema9_5m", 0.0) or 0.0
        vwap_val = getattr(bm, "vwap_session", 0.0) or 0.0
        btc_is_bull = (btc_state.mid_price >= ema20_val) and (btc_state.mid_price >= vwap_val) if (ema20_val > 0 and vwap_val > 0) else (btc_state.mid_price >= vwap_val)
        if ema9_5m > 0:
            btc_is_bull = btc_is_bull and (btc_state.mid_price >= ema9_5m)

        if btc_is_bull:
            expected_alt_move = m.beta_btc * r_btc
            lag_spread = expected_alt_move - r_alt

            if (
                r_btc >= dyn_impulse
                and tfi_btc >= dyn_tfi
                and r_alt <= self.cfg.e3_alt_reaction_cap * expected_alt_move
                and lag_spread >= dyn_min_lag
            ):
                entry = state.best_bid + state.meta.tick_size if state.spread_ticks >= 2.0 else state.best_bid
                tp1 = entry * (1.0 + max(0.0085, 1.10 * lag_spread))
                tp2 = entry * (1.0 + max(0.0180, 2.20 * lag_spread))
                sl = entry * (1.0 - max(0.0045, 0.60 * lag_spread))

                return SignalIntent(
                    engine_id="E3_LEADLAG",
                    priority=2,
                    symbol=state.symbol,
                    side="Buy",
                    entry_price=entry,
                    sl_price=sl,
                    tp1_price=tp1,
                    tp2_price=tp2,
                    max_hold_sec=self.cfg.e3_max_hold_sec,
                    leader_ref_ret=r_btc,
                    leader_entry_mid=btc_state.mid_price,
                    reason=f"LONG BTC_r={r_btc*100:.2f}% LagS={lag_spread*100:.2f}% Beta={m.beta_btc:.2f}",
                )

        # 2. SHORT LEAD-LAG (Медвежий дамп BTC - строго подтвержденный ниже VWAP и EMA20)
        btc_is_bear = (btc_state.mid_price <= ema20_val) and (btc_state.mid_price <= vwap_val) if (ema20_val > 0 and vwap_val > 0) else (btc_state.mid_price <= vwap_val)
        if ema9_5m > 0:
            btc_is_bear = btc_is_bear and (btc_state.mid_price <= ema9_5m)

        if btc_is_bear:
            expected_alt_drop = m.beta_btc * abs(r_btc)
            actual_alt_drop = abs(r_alt) if r_alt < 0 else 0.0
            lag_spread = expected_alt_drop - actual_alt_drop

            if (
                r_btc <= -dyn_impulse
                and tfi_btc <= -dyn_tfi
                and actual_alt_drop <= self.cfg.e3_alt_reaction_cap * expected_alt_drop
                and lag_spread >= dyn_min_lag
            ):
                entry = state.best_ask - state.meta.tick_size if state.spread_ticks >= 2.0 else state.best_ask
                tp1 = entry * (1.0 - max(0.0085, 1.10 * lag_spread))
                tp2 = entry * (1.0 - max(0.0180, 2.20 * lag_spread))
                sl = entry * (1.0 + max(0.0045, 0.60 * lag_spread))

                return SignalIntent(
                    engine_id="E3_LEADLAG",
                    priority=2,
                    symbol=state.symbol,
                    side="Sell",
                    entry_price=entry,
                    sl_price=sl,
                    tp1_price=tp1,
                    tp2_price=tp2,
                    max_hold_sec=self.cfg.e3_max_hold_sec,
                    leader_ref_ret=r_btc,
                    leader_entry_mid=btc_state.mid_price,
                    reason=f"SHORT BTC_r={r_btc*100:.2f}% LagS={lag_spread*100:.2f}% Beta={m.beta_btc:.2f}",
                )

        return None
