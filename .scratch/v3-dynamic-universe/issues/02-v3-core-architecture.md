# Issue 02: Core Engine v3 with Dynamic Universe Integration

Status: completed
Type: task

## Goal
Assemble `v3/src/core_v3.py` incorporating:
- Strict Point-in-Time (PIT) Donchian breakout evaluation.
- Weighted average entry price on anti-martingale pyramiding add.
- Basket cash breakeven stop loss.
- 3.5x gross portfolio leverage cap.
- Half-Kelly 2.0% risk sizing with 15% Soft DD and 30% Hard DD halts.
- Dynamic universe capacity management (never drop held positions during universe re-ranking).
Completed with transactional state persistence and accounting invariants.
