"""Experiment with short filtering and exit parameters."""
import sys
sys.path.insert(0, ".")
from decimal import Decimal
import pandas as pd

from src.config import (
    DEV_START_TS,
    DEV_END_TS,
    VAL_START_TS,
    VAL_END_TS,
    FINAL_START_TS,
    FINAL_END_TS,
    PARQUET_DATA_DIR,
    PERIOD_ENTRY_CUTOFF_MS,
    SYMBOLS,
    StrategyParams,
)
from src.data_downloader import DataDownloader
from src.strategy import StrategyV10
from src.simulator import MarketSimulator
from src.research_protocol import compute_metrics

df_1m_dict = {sym: pd.read_parquet(PARQUET_DATA_DIR / f"{sym}_1m.parquet") for sym in SYMBOLS}
funding_dict = {sym: pd.read_parquet(PARQUET_DATA_DIR / f"{sym}_funding.parquet") for sym in SYMBOLS}

downloader = DataDownloader()
df_1h_dict = {sym: downloader.aggregate_candles(df_1m_dict[sym], 1) for sym in SYMBOLS}
df_4h_dict = {sym: downloader.aggregate_candles(df_1m_dict[sym], 4) for sym in SYMBOLS}

# Let's inspect what happens across all 3 periods if we only take LONGS vs both
param = StrategyParams(N=24, K=2.5, RSI_LONG_MIN=55, RSI_SHORT_MAX=45)
strat = StrategyV10(params=param)
prepared = {sym: strat.prepare_data(df_1m_dict[sym], df_1h_dict[sym], df_4h_dict[sym]) for sym in SYMBOLS}

for period_name, start_ts, end_ts in [
    ("Development", DEV_START_TS, DEV_END_TS),
    ("Validation", VAL_START_TS, VAL_END_TS),
    ("Final Test", FINAL_START_TS, FINAL_END_TS),
]:
    sim = MarketSimulator(strategy_params=param, initial_capital=Decimal("300.0"))
    res = sim.run(df_1m_dict, prepared, funding_dict, start_ts, end_ts, PERIOD_ENTRY_CUTOFF_MS)
    m = compute_metrics(res)
    
    longs = [t for t in res.trades if t.direction == "LONG"]
    shorts = [t for t in res.trades if t.direction == "SHORT"]
    long_pnl = sum(float(t.net_pnl) for t in longs)
    short_pnl = sum(float(t.net_pnl) for t in shorts)
    long_wr = (sum(1 for t in longs if t.net_pnl > 0) / len(longs) * 100) if longs else 0
    short_wr = (sum(1 for t in shorts if t.net_pnl > 0) / len(shorts) * 100) if shorts else 0
    
    print(f"\n[{period_name}] Total: {m['total_trades']} trades, NetPnL=${m['net_profit']:.2f}, PF={m['profit_factor']:.2f}, MaxDD={m['max_drawdown_pct']:.1f}%")
    print(f"  LONGS:  {len(longs)} trades, PnL=${long_pnl:.2f}, WR={long_wr:.1f}%")
    print(f"  SHORTS: {len(shorts)} trades, PnL=${short_pnl:.2f}, WR={short_wr:.1f}%")
