"""Analyze Final Test period for No BE, TP=3.0x."""
import sys
sys.path.insert(0, ".")
from decimal import Decimal
import pandas as pd

from src.config import (
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
import src.simulator as sim_module

df_1m_dict = {sym: pd.read_parquet(PARQUET_DATA_DIR / f"{sym}_1m.parquet") for sym in SYMBOLS}
funding_dict = {sym: pd.read_parquet(PARQUET_DATA_DIR / f"{sym}_funding.parquet") for sym in SYMBOLS}

downloader = DataDownloader()
df_1h_dict = {sym: downloader.aggregate_candles(df_1m_dict[sym], 1) for sym in SYMBOLS}
df_4h_dict = {sym: downloader.aggregate_candles(df_1m_dict[sym], 4) for sym in SYMBOLS}

param = StrategyParams(N=24, K=2.0, RSI_LONG_MIN=55, RSI_SHORT_MAX=45)
strat = StrategyV10(params=param)
prepared = {sym: strat.prepare_data(df_1m_dict[sym], df_1h_dict[sym], df_4h_dict[sym]) for sym in SYMBOLS}

sim_module.BREAKEVEN_ATR_MULT = 999.0  # No BE
sim = MarketSimulator(strategy_params=param, initial_capital=Decimal("300.0"))
sim.profile = sim.profile.__class__(**{**sim.profile.__dict__, "tp_atr_mult": 3.0})
res = sim.run(df_1m_dict, prepared, funding_dict, FINAL_START_TS, FINAL_END_TS, PERIOD_ENTRY_CUTOFF_MS)

longs = [t for t in res.trades if t.direction == "LONG"]
shorts = [t for t in res.trades if t.direction == "SHORT"]

print(f"Total trades: {len(res.trades)}, Net PnL: ${float(res.net_profit):.2f}")
print(f"Longs:  {len(longs)} trades, PnL: ${sum(float(t.net_pnl) for t in longs):.2f}, WR: {sum(1 for t in longs if t.net_pnl > 0)/len(longs)*100 if longs else 0:.1f}%")
print(f"Shorts: {len(shorts)} trades, PnL: ${sum(float(t.net_pnl) for t in shorts):.2f}, WR: {sum(1 for t in shorts if t.net_pnl > 0)/len(shorts)*100 if shorts else 0:.1f}%")

for sym in SYMBOLS:
    sym_trades = [t for t in res.trades if t.symbol == sym]
    print(f"{sym}: {len(sym_trades)} trades, PnL: ${sum(float(t.net_pnl) for t in sym_trades):.2f}")
