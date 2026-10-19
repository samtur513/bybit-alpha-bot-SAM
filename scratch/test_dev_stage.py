"""Quick test of Stage 1 Development with Strategy v1.0."""
import logging
import sys
sys.path.insert(0, ".")
from decimal import Decimal
import pandas as pd

from src.config import (
    DEV_START_TS,
    DEV_END_TS,
    PARAMETER_GRID,
    PARQUET_DATA_DIR,
    PERIOD_ENTRY_CUTOFF_MS,
    SYMBOLS,
    StrategyParams,
)
from src.data_downloader import DataDownloader
from src.strategy import StrategyV10
from src.simulator import MarketSimulator
from src.research_protocol import compute_metrics

print("Loading data...")
df_1m_dict = {}
funding_dict = {}
for sym in SYMBOLS:
    df_1m_dict[sym] = pd.read_parquet(PARQUET_DATA_DIR / f"{sym}_1m.parquet")
    f_path = PARQUET_DATA_DIR / f"{sym}_funding.parquet"
    funding_dict[sym] = pd.read_parquet(f_path) if f_path.exists() else pd.DataFrame()

downloader = DataDownloader()
df_1h_dict = {}
df_4h_dict = {}
for sym in SYMBOLS:
    df_1h_dict[sym] = downloader.aggregate_candles(df_1m_dict[sym], 1)
    df_4h_dict[sym] = downloader.aggregate_candles(df_1m_dict[sym], 4)

cached_prepared_by_n = {}
df_1h_prepared_by_params = {}

for p in PARAMETER_GRID:
    if p.N not in cached_prepared_by_n:
        print(f"Preparing data for N={p.N}...")
        strat = StrategyV10(params=p)
        cached_prepared_by_n[p.N] = {}
        for sym in SYMBOLS:
            cached_prepared_by_n[p.N][sym] = strat.prepare_data(
                df_1m_dict[sym], df_1h_dict[sym], df_4h_dict[sym]
            )
    df_1h_prepared_by_params[p] = cached_prepared_by_n[p.N]

test_grid = PARAMETER_GRID[:6]
print(f"\nRunning Dev simulations for first {len(test_grid)} parameter sets...")
results = []
for p in test_grid:
    sim = MarketSimulator(strategy_params=p, initial_capital=Decimal("300.0"))
    res = sim.run(
        df_1m_dict,
        df_1h_prepared_by_params[p],
        funding_dict,
        start_ts=DEV_START_TS,
        end_ts=DEV_END_TS,
        entry_cutoff_ms=PERIOD_ENTRY_CUTOFF_MS,
    )
    m = compute_metrics(res)
    score = (m["net_profit"] / m["max_drawdown_pct"]) if m["max_drawdown_pct"] > 0 else 0.0
    results.append((p, m, score))
    print(f"N={p.N:2d}, K={p.K:.1f}, RSI={p.RSI_LONG_MIN}/{p.RSI_SHORT_MAX} -> Trades={m['total_trades']:3d}, WR={m['win_rate']:4.1f}%, NetPnL=${m['net_profit']:6.2f}, Ret={m['return_pct']:5.1f}%, MaxDD={m['max_drawdown_pct']:4.1f}%, PF={m['profit_factor']:4.2f}, Score={score:6.3f}")

print("\n--- Summary of profitable variants ---")
profitable = [r for r in results if r[1]["net_profit"] > 0]
print(f"Profitable variants: {len(profitable)} / {len(results)}")
for p, m, score in sorted(profitable, key=lambda x: x[2], reverse=True):
    print(f"  N={p.N}, K={p.K}, RSI={p.RSI_LONG_MIN}/{p.RSI_SHORT_MAX} | PnL=${m['net_profit']:.2f} | PF={m['profit_factor']:.2f} | DD={m['max_drawdown_pct']:.1f}% | Trades={m['total_trades']}")
