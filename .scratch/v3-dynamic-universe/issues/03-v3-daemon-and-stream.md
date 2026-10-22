# Issue 03: Paper Trader Daemon v3 and News Core Defensive Shield

Status: completed
Type: task

## Goal
Build `v3/src/paper_trader_v3.py` uniting:
- Dynamic WebSocket subscription rotation based on screener outputs (`update_symbols`).
- News Core v1.1 Defensive Shield (`NewsConfig.defensive_only()`):
  - Regime Guard macro embargo check (FOMC, CPI, NFP, USDC depeg).
  - Emergency Eject (Long + CRASH_SHOCK).
  - Squeeze Escape (Short + MAJOR_CATALYST).
  - Runner Protection ($R \ge 1.5R$ -> trail stop, no panic dump).
  - Soft Defense for Tier 3 media rumors.
- Atomic state snapshots to `BOT_STATE_PERSISTENT_V3.json`.
Completed and integrated.
