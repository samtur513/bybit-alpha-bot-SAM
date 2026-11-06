"""
Layer 6: Regime-Adaptive 4-Stage Exit State Machine (AdaptiveExitFSM).
Specification from NewDragon v4.0 Universal.

Implements:
- Stage 0: Initial (-1.0R) & Fast Scratch (Inside Bars + Adverse OFI -> Zero Loss Exit)
- Stage 1: Ratchet Lock at +0.85R (Tighten stop from -1.0R to -0.35R)
- Stage 2: Partial Scale-Out at +1.40R (Close 60% via Maker limit + Move stop to +0.35R profit lock)
- Stage 3: Adaptive Chandelier Trail at +2.00R (Dynamic compression 2.5x -> 1.2x on shock or adverse order flow)
"""
from typing import Optional, Dict, Any
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
        trend_score: float = 0.0,
        is_strong_trend: bool = False,
        scratch_z_ofi_threshold: float = -1.5,
        ker: float = 0.0,
    ) -> ExitAction:
        """
        Evaluates incoming bar against open position and returns ExitAction.
        Features Dual-Profile (Impulse Pyramid at KER >= 0.40 vs Mean-Reversion),
        Dynamic Stage 2 Sizing, MFE Pullback Breakeven, and Adaptive Chandelier.
        """
        direction = 1.0 if pos.side.upper() == "LONG" else -1.0
        initial_risk = max(pos.initial_1r_dist, 1e-9)
        unrealized_dist = (close - pos.entry_price) * direction
        unrealized_r = unrealized_dist / initial_risk

        # 1. Update Peak Favorable Price and Peak R (for MFE Pullback Tracking)
        if pos.side.upper() == "LONG":
            pos.max_favor_price = max(pos.max_favor_price, high)
            peak_r = (pos.max_favor_price - pos.entry_price) / initial_risk
        else:
            if pos.max_favor_price <= 0.0:
                pos.max_favor_price = low
            else:
                pos.max_favor_price = min(pos.max_favor_price, low)
            peak_r = (pos.entry_price - pos.max_favor_price) / initial_risk

        # 2. Update Inside Bar Counter (for Stage 0 Fast Scratch)
        if high <= prev_high and low >= prev_low:
            pos.inside_bar_count += 1
        else:
            pos.inside_bar_count = 0

        # Stage 0 -> Fast Scratch:
        # If 2 consecutive inside bars, unrealized profit hasn't reached +0.6R,
        # and order flow imbalance turns against position.
        adverse_ofi = (pos.side.upper() == "LONG" and z_ofi < scratch_z_ofi_threshold) or (
            pos.side.upper() == "SHORT" and z_ofi > abs(scratch_z_ofi_threshold)
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
            # Dynamic compression: shock or adverse order flow
            severe_adverse = (shock_prob >= 0.25) or (direction * z_ofi < -2.5)
            if severe_adverse:
                eff_mult = self.chand_compressed  # 1.2
            elif is_strong_trend or trend_score >= 0.25:
                eff_mult = 3.0  # Widened trail (3.0x ATR) to give big runners room!
            else:
                eff_mult = self.chand_mult  # 2.5

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

        # Stage 2: Dual-Profile Logic at +1.40R
        # Profile A (IMPULSE_PYRAMID at KER >= 0.40):
        # In powerful trend, do NOT close partial! Keep 100% volume for pyramiding,
        # lock stop at +0.50R profit lock.
        # Profile B (MEAN_REVERSION at KER < 0.40):
        # Dynamic partial scale-out (40-70% Maker fee) + Profit-Lock (+0.35R).
        if unrealized_r >= self.s2_r and pos.stage < 2:
            pos.stage = 2

            if ker >= 0.40:
                # IMPULSE_PYRAMID Profile:
                new_stop = pos.entry_price + direction * (0.50 * initial_risk)
                pos.current_stop = new_stop
                return ExitAction(
                    action_type="UPDATE_STOP",
                    new_stop=new_stop,
                    close_qty_ratio=0.0,
                    reason="stage2_impulse_pyramid_lock",
                )

            # MEAN_REVERSION Profile:
            new_stop = pos.entry_price + direction * (self.s2_lock * initial_risk)
            pos.current_stop = new_stop

            # Dynamic Stage 2 Sizing:
            if is_strong_trend or trend_score >= 0.25:
                close_pct = 0.40
            elif trend_score > 0.0 and trend_score < 0.10:
                close_pct = 0.70
            else:
                close_pct = self.s2_pct

            return ExitAction(
                action_type="PARTIAL_CLOSE_AND_STOP",
                new_stop=new_stop,
                close_qty_ratio=close_pct,
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

        # Stage 1 MFE Pullback Breakeven:
        # If position gained >= +1.0R (peak_r >= 1.0) and pulled back >= 35% from peak,
        # lock Breakeven + 0.20% (covering fees) so a winning trade never turns into a loss.
        if pos.stage == 1 and peak_r >= 1.0:
            pullback_r = peak_r - unrealized_r
            if pullback_r >= (peak_r * 0.35) and unrealized_r > 0.15:
                be_stop = pos.entry_price + direction * (0.0020 * pos.entry_price)
                if (direction == 1.0 and be_stop > pos.current_stop) or (direction == -1.0 and be_stop < pos.current_stop):
                    pos.current_stop = be_stop
                    return ExitAction(
                        action_type="UPDATE_STOP",
                        new_stop=be_stop,
                        reason="stage1_mfe_pullback_be",
                    )

        return ExitAction(action_type="HOLD")

    def update(self, pos: PositionTracker, market: Dict[str, Any]) -> ExitAction:
        """Dictionary-driven wrapper around evaluate_bar for compatibility."""
        px = market.get("price", pos.entry_price)
        high = market.get("high", px)
        low = market.get("low", px)
        prev_h = market.get("prev_high", high)
        prev_l = market.get("prev_low", low)
        high_20 = market.get("high_20", high)
        low_20 = market.get("low_20", low)
        atr = market.get("atr", pos.initial_1r_dist)
        z_ofi = market.get("z_ofi", 0.0)
        ker = market.get("ker", 0.0)
        shock = market.get("shock_prob", 0.0)
        trend_score = market.get("trend_score", 0.0)
        is_strong = market.get("is_strong_trend", ker >= 0.35)

        return self.evaluate_bar(
            pos=pos,
            high=high,
            low=low,
            close=px,
            prev_high=prev_h,
            prev_low=prev_l,
            highest_20=high_20,
            lowest_20=low_20,
            atr_1h=atr,
            z_ofi=z_ofi,
            shock_prob=shock,
            trend_score=trend_score,
            is_strong_trend=is_strong,
            ker=ker,
        )

