"""
Scalp Strategy Evaluators for v3.7-v4-exp:
Extends ThreeEnginesEvaluate with NewDragon v4 KER (Kaufman Efficiency Ratio) chop gate.
"""

from typing import Optional, Any
from v3_7.src.scalp_engines import ThreeEnginesEvaluate, SignalIntent
from v3_7_v4_exp.src.config import ScalperConfigV37V4
from v3_7_v4_exp.src.microstructure_v4 import SymbolMicrostructureStateV4


class ScalpEnginesV4(ThreeEnginesEvaluate):
    """
    Evaluates Scalp signals (E2 Sweeps & E3 Lead-Lag) with KER Chop Gate.
    """

    def __init__(self, cfg: ScalperConfigV37V4):
        super().__init__(cfg)
        self.cfg: ScalperConfigV37V4 = cfg

    def evaluate_engine2_liquidity_sweep(
        self,
        state: SymbolMicrostructureStateV4,
        btc_or_now: Any = None,
        now_or_btc: Any = None,
        btc_state: Optional[SymbolMicrostructureStateV4] = None,
        now_ms: Optional[int] = None,
    ) -> Optional[SignalIntent]:
        # KER Chop Gate: Do not trade sweeps if market is in choppy noise
        if hasattr(state, "get_ker") and state.get_ker() < getattr(self.cfg, "min_ker_threshold", 0.30):
            return None

        # Resolve btc_state and now_ms regardless of calling convention
        resolved_btc = btc_state
        resolved_now = now_ms

        if isinstance(btc_or_now, (int, float)):
            resolved_now = int(btc_or_now)
        elif btc_or_now is not None:
            resolved_btc = btc_or_now

        if isinstance(now_or_btc, (int, float)):
            resolved_now = int(now_or_btc)
        elif now_or_btc is not None:
            resolved_btc = now_or_btc

        if resolved_btc is None:
            return None
        if resolved_now is None:
            resolved_now = 0

        return super().evaluate_engine2_liquidity_sweep(state, resolved_btc, resolved_now)

    def evaluate_engine3_lead_lag(
        self,
        state: SymbolMicrostructureStateV4,
        btc_state: SymbolMicrostructureStateV4,
        now_ms: int,
    ) -> Optional[SignalIntent]:
        # Lead-lag beta catchup
        return super().evaluate_engine3_lead_lag(state, btc_state, now_ms)

    def evaluate_engine1_obi_breakout(
        self,
        state: SymbolMicrostructureStateV4,
        now_ms: int,
        btc_state: Optional[SymbolMicrostructureStateV4] = None,
    ) -> Optional[SignalIntent]:
        """
        Evaluates Engine 1 Order Flow Imbalance with NewDragon v4 KER Gate & Z_OFI Trigger.
        """
        # KER Chop Gate: Do not enter order flow breakout in choppy noise
        if hasattr(state, "get_ker") and state.get_ker() < getattr(self.cfg, "min_ker_threshold", 0.30):
            return None

        # Check classic OBI breakout first
        sig = super().evaluate_engine1_obi_breakout(state, now_ms, btc_state)
        if sig:
            return sig

        # Evaluate NewDragon v4 Z_OFI flow trigger
        z = getattr(state, "get_z_ofi", lambda: 0.0)()
        threshold = getattr(self.cfg, "z_ofi_entry_threshold", 2.0)
        if abs(z) < threshold:
            return None

        if state.mid_price <= 0 or state.spread_ticks > getattr(self.cfg, "e1_max_spread_ticks", 2.5):
            return None

        # BTC macro-trend confirmation:
        btc_ret_60s = 0.0
        if btc_state and hasattr(btc_state, "get_depletion_and_return"):
            btc_ret_60s, _, _ = btc_state.get_depletion_and_return(now_ms, 60000)

        btc_bear = True
        btc_bull = True
        if btc_state and btc_state.mid_price > 0:
            bm = getattr(btc_state, "meta", None)
            btc_ema20 = getattr(bm, "ema20_15m", 0.0) or 0.0
            btc_vwap = getattr(bm, "vwap_session", 0.0) or 0.0
            if btc_ema20 > 0 and btc_vwap > 0:
                btc_bull = btc_state.mid_price >= min(btc_ema20, btc_vwap)
                btc_bear = btc_state.mid_price <= max(btc_ema20, btc_vwap)
            elif btc_vwap > 0:
                btc_bull = btc_state.mid_price >= btc_vwap
                btc_bear = btc_state.mid_price <= btc_vwap
            elif btc_ema20 > 0:
                btc_bull = btc_state.mid_price >= btc_ema20
                btc_bear = btc_state.mid_price <= btc_ema20

        # Altcoin trend confirmation:
        m = state.meta
        alt_ema9 = getattr(m, "ema9_5m", 0.0) or 0.0
        alt_vwap = getattr(m, "vwap_session", 0.0) or 0.0
        alt_ret_5m, _, _ = state.get_depletion_and_return(now_ms, 300000) if hasattr(state, "get_depletion_and_return") else (0.0, 0.0, 0.0)

        # Alt must be confirmed bearish for shorting:
        # Veto shorts if alt is trading above VWAP/EMA9 (pumping altcoin protection)
        alt_bear = True
        if alt_vwap > 0 and alt_ema9 > 0:
            alt_bear = (state.mid_price <= alt_vwap or state.mid_price <= alt_ema9) and alt_ret_5m <= 0.0010
        elif alt_vwap > 0:
            alt_bear = (state.mid_price <= alt_vwap) and alt_ret_5m <= 0.0010
        elif alt_ema9 > 0:
            alt_bear = (state.mid_price <= alt_ema9) and alt_ret_5m <= 0.0010

        # Alt must not be crashing for longs:
        alt_bull = True
        if alt_vwap > 0 and alt_ema9 > 0:
            alt_bull = (state.mid_price >= alt_vwap or state.mid_price >= alt_ema9) or alt_ret_5m >= -0.0010
        elif alt_vwap > 0:
            alt_bull = (state.mid_price >= alt_vwap) or alt_ret_5m >= -0.0010

        natr = max(getattr(state.meta, "natr_5m", 0.0035), getattr(self.cfg, "e1_min_natr_5m", 0.0035))
        
        # Strict Scalper Stop & Take Profit Sizing:
        # Stop loss capped at 0.65% max (never allow wide stops like 3.6% on high-NATR alts)
        sl_dist_pct = min(0.0065, max(0.0035, 0.50 * natr))
        tp1_dist_pct = max(0.0045, 0.80 * natr)
        tp2_dist_pct = max(0.0100, 1.60 * natr)

        # 1. LONG on institutional order flow surge (+2.0 sigma)
        if z >= threshold and btc_ret_60s >= -0.0004 and alt_bull:
            entry = state.best_bid + state.meta.tick_size if state.spread_ticks >= 2.0 else state.best_bid
            tp1 = entry * (1.0 + tp1_dist_pct)
            tp2 = entry * (1.0 + tp2_dist_pct)
            sl = entry * (1.0 - sl_dist_pct)
            return SignalIntent(
                engine_id="E1_ZOFI",
                priority=3,
                symbol=state.symbol,
                side="Buy",
                entry_price=entry,
                sl_price=sl,
                tp1_price=tp1,
                tp2_price=tp2,
                max_hold_sec=getattr(self.cfg, "e1_max_hold_sec", 900.0),
                reason=f"LONG Z_OFI={z:+.2f}σ KER={state.get_ker():.2f}",
            )

        # 2. SHORT on institutional order flow dump (-2.0 sigma)
        # Strictly vetoed unless BTC and Altcoin are confirmed bearish
        if z <= -threshold and btc_ret_60s <= 0.0004 and btc_bear and alt_bear:
            entry = state.best_ask - state.meta.tick_size if state.spread_ticks >= 2.0 else state.best_ask
            tp1 = entry * (1.0 - tp1_dist_pct)
            tp2 = entry * (1.0 - tp2_dist_pct)
            sl = entry * (1.0 + sl_dist_pct)
            return SignalIntent(
                engine_id="E1_ZOFI",
                priority=3,
                symbol=state.symbol,
                side="Sell",
                entry_price=entry,
                sl_price=sl,
                tp1_price=tp1,
                tp2_price=tp2,
                max_hold_sec=getattr(self.cfg, "e1_max_hold_sec", 900.0),
                reason=f"SHORT Z_OFI={z:+.2f}σ KER={state.get_ker():.2f}",
            )

        return None

    # Backwards-compatible aliases
    evaluate_engine2_sweeps_reversion = evaluate_engine2_liquidity_sweep
    evaluate_engine3_leadlag_arbitrage = evaluate_engine3_lead_lag

    def evaluate_all(
        self,
        state: SymbolMicrostructureStateV4,
        btc_state: Optional[SymbolMicrostructureStateV4],
        now_ms: int,
    ) -> Optional[SignalIntent]:
        """Evaluates active engines in priority order."""
        if not btc_state:
            return None

        # 1. E2 Liquidity Sweeps
        if getattr(self.cfg, "enable_e2_sweeps", True):
            sig = self.evaluate_engine2_liquidity_sweep(state, btc_state, now_ms)
            if sig:
                return sig

        # 2. E3 Lead-Lag Arbitrage
        if getattr(self.cfg, "enable_e3_leadlag", True):
            sig = self.evaluate_engine3_lead_lag(state, btc_state, now_ms)
            if sig:
                return sig

        # 3. E1 Order Flow Imbalance (Z_OFI & OBI Breakout)
        if getattr(self.cfg, "enable_e1_obi", True):
            sig = self.evaluate_engine1_obi_breakout(state, now_ms, btc_state)
            if sig:
                return sig

        return None
