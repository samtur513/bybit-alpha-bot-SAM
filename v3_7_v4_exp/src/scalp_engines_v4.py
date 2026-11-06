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

        if getattr(self.cfg, "enable_e2_sweeps", True):
            sig = self.evaluate_engine2_liquidity_sweep(state, btc_state, now_ms)
            if sig:
                return sig

        if getattr(self.cfg, "enable_e3_leadlag", True):
            sig = self.evaluate_engine3_lead_lag(state, btc_state, now_ms)
            if sig:
                return sig

        return None
