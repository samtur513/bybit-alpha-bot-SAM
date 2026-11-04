"""
Layer 6: Regime-Adaptive 4-Stage Exit State Machine (AdaptiveExitFSM).
Specification from NewDragon v4.0 Universal.

Implements:
- Stage 0: Initial (-1.0R) & Fast Scratch (Inside Bars + Adverse OFI -> Zero Loss Exit)
- Stage 1: Ratchet Lock at +0.85R (Tighten stop from -1.0R to -0.35R)
- Stage 2: Partial Scale-Out at +1.40R (Close 60% via Maker limit + Move stop to +0.35R profit lock)
- Stage 3: Adaptive Chandelier Trail at +2.00R (Dynamic compression 2.5x -> 1.2x on shock or adverse order flow)
"""
from typing import Optional
from core.models import PositionTracker, ExitAction


class AdaptiveExitFSM:
    """
    Four-stage execution exit state machine with microstructural and shock awareness.
    """

    def __init__(
        self,
        stage1_r: float = 0.85,
        stage1_lock_r: float = -0.35,
        stage2_r: float = 1.40,
        stage2_close_pct: float = 0.60,
        stage2_lock_r: float = 0.35,
        stage3_r: float = 2.00,
        chandelier_mult: float = 2.5,
        chandelier_compressed_mult: float = 1.2,
    ) -> None:
        self.s1_r = stage1_r
        self.s1_lock = stage1_lock_r
        self.s2_r = stage2_r
        self.s2_pct = stage2_close_pct
        self.s2_lock = stage2_lock_r
        self.s3_r = stage3_r
        self.chand_mult = chandelier_mult
        self.chand_compressed = chandelier_compressed_mult

    def evaluate_bar(
        self,
        pos: PositionTracker,
        high: float,
        low: float,
        close: float,
        prev_high: float,
        prev_low: float,
        highest_20: float,
        lowest_20: float,
        atr_1h: float,
        z_ofi: float = 0.0,
        shock_prob: float = 0.0,
    ) -> ExitAction:
        """
        Evaluates incoming bar against open position and returns ExitAction.
        """
        direction = 1.0 if pos.side.upper() == "LONG" else -1.0
        initial_risk = max(pos.initial_1r_dist, 1e-9)
        unrealized_dist = (close - pos.entry_price) * direction
        unrealized_r = unrealized_dist / initial_risk

        # Update Inside Bar Counter (for Stage 0 Fast Scratch)
        if high <= prev_high and low >= prev_low:
            pos.inside_bar_count += 1
        else:
            pos.inside_bar_count = 0

        # Stage 0 -> Fast Scratch:
        # If 2 consecutive inside bars, unrealized profit hasn't reached +0.6R,
        # and order flow imbalance turns against position.
        adverse_ofi = (pos.side.upper() == "LONG" and z_ofi < -1.5) or (
            pos.side.upper() == "SHORT" and z_ofi > 1.5
        )
        if pos.stage == 0 and pos.inside_bar_count >= 2 and unrealized_r < 0.60 and adverse_ofi:
            return ExitAction(
                action_type="FULL_CLOSE_SCRATCH",
                close_qty_ratio=1.0,
                reason="fast_scratch_inside_bars",
            )

        # Stage 3: Chandelier Trailing Stop upon reaching +2.0R
        if unrealized_r >= self.s3_r or pos.stage == 3:
            pos.stage = 3
            # Dynamic compression: if shock probability spikes or order flow strongly adverse
            severe_adverse = (shock_prob >= 0.25) or (direction * z_ofi < -2.5)
            eff_mult = self.chand_compressed if severe_adverse else self.chand_mult

            if pos.side.upper() == "LONG":
                cand_stop = highest_20 - eff_mult * atr_1h
                if cand_stop > pos.current_stop:
                    pos.current_stop = cand_stop
                    return ExitAction(
                        action_type="UPDATE_STOP",
                        new_stop=cand_stop,
                        reason="stage3_chandelier",
                    )
            else:
                cand_stop = lowest_20 + eff_mult * atr_1h
                if cand_stop < pos.current_stop:
                    pos.current_stop = cand_stop
                    return ExitAction(
                        action_type="UPDATE_STOP",
                        new_stop=cand_stop,
                        reason="stage3_chandelier",
                    )
            return ExitAction(action_type="HOLD")

        # Stage 2: Partial Scale-Out (60% close at Maker fee) + Profit-Lock (+0.35R)
        if unrealized_r >= self.s2_r and pos.stage < 2:
            pos.stage = 2
            new_stop = pos.entry_price + direction * (self.s2_lock * initial_risk)
            pos.current_stop = new_stop
            return ExitAction(
                action_type="PARTIAL_CLOSE_AND_STOP",
                new_stop=new_stop,
                close_qty_ratio=self.s2_pct,
                reason="stage2_partial_scaleout",
            )

        # Stage 1: Ratchet Lock (+0.85R -> -0.35R stop)
        if unrealized_r >= self.s1_r and pos.stage < 1:
            pos.stage = 1
            new_stop = pos.entry_price + direction * (self.s1_lock * initial_risk)
            pos.current_stop = new_stop
            return ExitAction(
                action_type="UPDATE_STOP",
                new_stop=new_stop,
                reason="stage1_ratchet",
            )

        return ExitAction(action_type="HOLD")
