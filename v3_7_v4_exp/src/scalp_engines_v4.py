"""
Scalp Strategy Evaluators for v3.7-v4-exp:
Extends ThreeEnginesEvaluate with NewDragon v4 KER (Kaufman Efficiency Ratio) chop gate.
"""

from typing import Optional
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

    def evaluate_engine2_sweeps_reversion(
        self,
        state: SymbolMicrostructureStateV4,
        now_ms: int,
        btc_state: Optional[SymbolMicrostructureStateV4] = None,
    ) -> Optional[SignalIntent]:
        # KER Chop Gate: Do not trade sweeps if market is in choppy noise
        if hasattr(state, "get_ker") and state.get_ker() < self.cfg.min_ker_threshold:
            return None
        return super().evaluate_engine2_sweeps_reversion(state, now_ms, btc_state)

    def evaluate_engine3_leadlag_arbitrage(
        self,
        state: SymbolMicrostructureStateV4,
        btc_state: SymbolMicrostructureStateV4,
        now_ms: int,
    ) -> Optional[SignalIntent]:
        # Lead-lag beta catchup
        return super().evaluate_engine3_leadlag_arbitrage(state, btc_state, now_ms)

    def evaluate_all(
        self,
        state: SymbolMicrostructureStateV4,
        btc_state: Optional[SymbolMicrostructureStateV4],
        now_ms: int,
    ) -> Optional[SignalIntent]:
        """Evaluates active engines in priority order."""
        if self.cfg.enable_e2_sweeps:
            sig = self.evaluate_engine2_sweeps_reversion(state, now_ms, btc_state)
            if sig:
                return sig

        if self.cfg.enable_e3_leadlag and btc_state:
            sig = self.evaluate_engine3_leadlag_arbitrage(state, btc_state, now_ms)
            if sig:
                return sig

        return None
