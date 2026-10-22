# Issue 04: Comprehensive Test Suite for v3

Status: completed
Type: task

## Goal
Build comprehensive test suite under `v3/tests/`:
- `test_v3_universe_screener.py` (filters, rankings, edge cases) - 7 passed.
- `test_v3_core.py` (accounting invariants, pyramiding average entry, leverage cap) - 8 passed.
- `test_v3_news_defense.py` (regime guard blackout, eject, squeeze, runner protection) - 8 passed.
- `test_v3_accounting_invariants.py` (hostile inputs, latch halts, rollback) - 6 passed.
Total: 29 passed out of 29 (100% test pass).
