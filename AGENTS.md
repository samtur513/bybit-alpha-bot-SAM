# AGENTS.md

Instructions, conventions, and configuration for AI agents working in this repository.

## Project Overview

- **Project**: Bybit Linear Perpetuals Multi-Alpha Quantitative Trading Engine (v3.0 Production Freeze) with Dynamic Universe Screener and News Core Defensive Shield.
- **Runtime**: Python 3.12, Bybit Linear Perpetuals WebSocket/REST, PyTest.
- **Architecture**:
  - **v3.0 (Production Freeze, Port 8082)**: Dynamic Universe Screener (400+ Bybit Linear Perpetuals filtered by Liquidity, Normalized ATR, Kaufman Efficiency Ratio, Relative Strength, and Funding Rate), 4h Donchian Breakout, triple trend filter (ADX >= 18.0, EMA50 > EMA200, BTC Delta), anti-martingale pyramiding with penny-perfect weighted average entry price, News Core v1.1 defensive shields (Regime Guard, Emergency Eject, Squeeze Escape, Runner Protection), and Half-Kelly risk management.
  - **v2.1 (Port 8081)**: 4h Donchian Breakout with static 18-coin universe and News Core defensive shield.
  - **v2.0 (Port 8080)**: 1h Breakout baseline engine.
- **Key Directories**:
  - `v3/src/`: Production engine (`core_v3.py`), dynamic screener (`universe_screener.py`), paper trader daemon (`paper_trader_v3.py`), indicators (`indicators.py`), risk manager (`kelly_sizing.py`), pyramiding engine (`pyramiding_engine.py`), Bybit market stream (`market_stream.py`), and configuration (`config.py`).
  - `v3/src/news_core/`: Self-contained News Core v1.1 module (models, classifier, market confirmation, position matrix, regime guard, replay engine).
  - `v3/tests/`: Comprehensive unit, accounting invariant, and news defense test suites (30 tests, 100% pass).
  - `v3/reports/`: Full quantitative backtest, walk-forward validation (WFV), Bybit Replay protocol, and Monte Carlo audit reports.
  - `docs/adr/`: Architecture Decision Records (`0001`, `0002`).
  - `docs/agents/`: Agent conventions and tracker configurations.
  - `.scratch/`: Local markdown task and feature specifications.

## Agent skills

### Issue tracker

Local markdown issues tracked in `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical 5-role triage vocabulary (`needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context domain documentation (`GLOSSARY.md` + `docs/adr/`). See `docs/agents/domain.md`.

## Engineering Principles

1. **Trade Accounting Integrity**: Never compute pyramiding profit from the original entry price; always use mathematically rigorous weighted average entry price. Maintain penny-perfect conservation between closed trades net PnL and realized equity.
2. **Zero-Lookahead Point-in-Time (PIT)**: Never leak future prices or indicators. Donchian channels must use `shift(1)` on closed bars. Bar $t$ closes before bar $t+1$ executes.
3. **Deep Module Design**: Keep interfaces thin and implementations deep. Changes in execution or risk modules must not bleed into signal generation.
4. **Test-Driven Verification**: Every new feature or fix must be accompanied by unit tests and invariant checks. Never mark a task complete without 100% test pass.
5. **Windows Concurrency & Persistence**: File writes must be atomic with exponential backoff retries (`persistence.py`) to prevent Windows OS file lock collisions (`WinError 5`). Web API endpoints must serve live telemetry from memory cache.
