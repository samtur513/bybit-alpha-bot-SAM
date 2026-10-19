"""Analyze symbol breakdown for shorts across 2 years."""
import sys
sys.path.insert(0, ".")
from decimal import Decimal
import pandas as pd

from src.config import (
    DEV_START_TS,
    FINAL_END_TS,
    PARQUET_DATA_DIR,
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

sim_module.BREAKEVEN_ATR_MULT = 999.0
sim = MarketSimulator(strategy_params=param, initial_capital=Decimal("300.0"))
sim.profile = sim.profile.__class__(**{**sim.profile.__dict__, "tp_atr_mult": 3.0})

orig_gen = sim.strategy.generate_signals_for_bar
def strict_short_signals(row, symbol):
    sig = orig_gen(row, symbol)
    if sig and sig.direction == -1:
        if sig.adx_value < 30:
            return None
    return sig
sim.strategy.generate_signals_for_bar = strict_short_signals

res = sim.run(df_1m_dict, prepared, funding_dict, DEV_START_TS, FINAL_END_TS)

print(f"2-Year Continuous: Trades={len(res.trades)}, Net PnL=${float(res.net_profit):.2f}, Ret={float(res.return_pct):.1f}%, MaxDD={float(res.max_drawdown_pct):.1f}%")

for s in SYMBOLS:
    for d in ["LONG", "SHORT"]:
        t_sub = [t for t in res.trades if t.symbol == s and t.direction == d]
        if t_sub:
            pnl = sum(float(t.net_pnl) for t in t_sub)
            wr = sum(1 for t in t_sub if t.net_pnl > 0) / len(t_sub) * 100
            print(f"  {d:5s} {s:7s}: {len(t_sub):3d} trades, PnL=${pnl:6.2f}, WR={wr:4.1f}%")
