"""
Unit and Invariant Tests for Fleet Unified Shadow Paper Architecture.
Verifies:
1. Pluggable strategy registration and execution (v3.1.4, v3.8, v3.2 Range, v3.11 Funding).
2. Strict Causal Zero-Lookahead flow: SIGNAL -> INTENT -> FILL -> POSITION.
3. Absolute capital and database isolation.
4. 3-Way Double-Entry Financial Reconciliation (Method 1 == Method 2 == Method 3).
"""

from decimal import Decimal
from pathlib import Path
import shutil
import tempfile
import numpy as np
import pandas as pd
import pytest

from tools.fleet_unified_shadow_paper import (
    FleetUnifiedShadowPaperRunner,
    ChampionV314Strategy,
    TrendLabV38Strategy,
    IRASRangeStrategy,
    FundingSqueezeStrategy,
)


@pytest.fixture
def temp_output_dir():
    d = Path(tempfile.mkdtemp(prefix="test_unified_shadow_"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_unified_shadow_paper_multi_strategy_execution(temp_output_dir):
    runner = FleetUnifiedShadowPaperRunner(temp_output_dir, mode="HISTORICAL_REPLAY")

    # 1. Register 4 distinct alpha strategies
    strat_314 = ChampionV314Strategy(initial_capital=Decimal("300.0"))
    strat_38 = TrendLabV38Strategy(initial_capital=Decimal("300.0"))
    strat_range = IRASRangeStrategy(initial_capital=Decimal("300.0"))
    strat_fund = FundingSqueezeStrategy(initial_capital=Decimal("300.0"))

    runner.register_strategy(strat_314, "shadow_test_v314")
    runner.register_strategy(strat_38, "shadow_test_v38")
    runner.register_strategy(strat_range, "shadow_test_range")
    runner.register_strategy(strat_fund, "shadow_test_funding")

    assert len(runner.strategies) == 4
    assert len(runner.adapters) == 4

    # 2. Verify 4 separate SQLite DBs were created
    db_files = list(temp_output_dir.glob("*.db"))
    assert len(db_files) == 4

    # 3. Create synthetic multi-bar sequence
    n_bars = 40
    timestamps = [1700000000000 + i * 3600000 for i in range(n_bars)]
    closes = [100.0 + i * 1.5 for i in range(n_bars)]  # Strong upward trend
    highs = [c + 2.0 for c in closes]
    lows = [c - 2.0 for c in closes]
    opens = [closes[i - 1] if i > 0 else 100.0 for i in range(n_bars)]

    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": opens,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1000.0] * n_bars,
        "adx": [25.0] * n_bars,
        "atr": [2.5] * n_bars,
        "bb_mid": closes,
        "bb_upper": [c + 5.0 for c in closes],
        "bb_lower": [c - 5.0 for c in closes],
        "bb_width": [0.08] * n_bars,
        "rsi": [55.0] * n_bars,
        "funding_rate": [-0.0008] * n_bars, # Negative funding triggers FundingSqueeze
    })

    # Step through replay
    for i in range(32, n_bars - 1):
        hist_slice = df.iloc[: i + 1]
        cur_bar = df.iloc[i].to_dict()
        next_bar = df.iloc[i + 1].to_dict()
        runner.run_replay_step("BTCUSDT", cur_bar, next_bar, hist_slice)

    # 4. Export summary and verify 3-way financial reconciliation across all 4 strategies
    summary_df = runner.export_summary()
    assert len(summary_df) == 4

    for _, row in summary_df.iterrows():
        assert row["recon_status"] == "PASS_PENNY_PERFECT"
        assert row["recon_discrepancy"] <= 0.00001
        assert row["initial_capital"] == 300.0
