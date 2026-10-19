"""Test No BE, TP=3.0x across all 3 periods."""
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
import src.simulator as sim_module

df_1m_dict = {sym: pd.read_parquet(PARQUET_DATA_DIR / f"{sym}_1m.parquet") for sym in SYMBOLS}
funding_dict = {sym: pd.read_parquet(PARQUET_DATA_DIR / f"{sym}_funding.parquet") for sym in SYMBOLS}

downloader = DataDownloader()
df_1h_dict = {sym: downloader.aggregate_candles(df_1m_dict[sym], 1) for sym in SYMBOLS}
df_4h_dict = {sym: downloader.aggregate_candles(df_1m_dict[sym], 4) for sym in SYMBOLS}

param = StrategyParams(N=24, K=2.0, RSI_LONG_MIN=55, RSI_SHORT_MAX=45)
strat = StrategyV10(params=param)
prepared = {sym: strat.prepare_data(df_1m_dict[sym], df_1h_dict[sym], df_4h_dict[sym]) for sym in SYMBOLS}

for config_name, be_val in [("No BE, TP=3.0x", 999.0), ("BE=2.0, TP=3.0x", 2.0), ("BE=1.5, TP=3.0x", 1.5)]:
    print(f"\n==================== {config_name} ====================")
    sim_module.BREAKEVEN_ATR_MULT = be_val
    
    total_pnl_2y = 0.0
    for period_name, start_ts, end_ts in [
        ("Development", DEV_START_TS, DEV_END_TS),
        ("Validation", VAL_START_TS, VAL_END_TS),
        ("Final Test", FINAL_START_TS, FINAL_END_TS),
    ]:
        sim = MarketSimulator(strategy_params=param, initial_capital=Decimal("300.0"))
        sim.profile = sim.profile.__class__(**{**sim.profile.__dict__, "tp_atr_mult": 3.0})
        res = sim.run(df_1m_dict, prepared, funding_dict, start_ts, end_ts, PERIOD_ENTRY_CUTOFF_MS)
        m = compute_metrics(res)
        total_pnl_2y += m["net_profit"]
        print(f"[{period_name:11s}] PnL=${m['net_profit']:6.2f} ({m['return_pct']:5.1f}%), PF={m['profit_factor']:4.2f}, MaxDD={m['max_drawdown_pct']:4.1f}%, Trades={m['total_trades']:2d}, WR={m['win_rate']:4.1f}%")
    print(f"-> 2-Year Total across independent periods: ${total_pnl_2y:.2f}")
