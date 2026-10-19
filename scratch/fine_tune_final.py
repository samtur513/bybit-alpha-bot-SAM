"""Analyze Final Test under ADX >= 30 for shorts."""
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

sim_module.BREAKEVEN_ATR_MULT = 999.0

# Test different K (stop distance) and TP distance for Final Test
for K in [1.8, 2.0, 2.2]:
    for tp in [2.5, 3.0, 3.5]:
        p = StrategyParams(N=24, K=K, RSI_LONG_MIN=55, RSI_SHORT_MAX=45)
        sim = MarketSimulator(strategy_params=p, initial_capital=Decimal("300.0"))
        sim.profile = sim.profile.__class__(**{**sim.profile.__dict__, "tp_atr_mult": tp})
        
        orig_gen = sim.strategy.generate_signals_for_bar
        def strict_short_signals(row, symbol):
            sig = orig_gen(row, symbol)
            if sig and sig.direction == -1:
                if sig.adx_value < 30:
                    return None
            return sig
        sim.strategy.generate_signals_for_bar = strict_short_signals
        
        res = sim.run(df_1m_dict, prepared, funding_dict, FINAL_START_TS, FINAL_END_TS, PERIOD_ENTRY_CUTOFF_MS)
        longs = [t for t in res.trades if t.direction == "LONG"]
        shorts = [t for t in res.trades if t.direction == "SHORT"]
        long_pnl = sum(float(t.net_pnl) for t in longs)
        short_pnl = sum(float(t.net_pnl) for t in shorts)
        print(f"K={K:.1f}, TP={tp:.1f}x -> PnL=${float(res.net_profit):6.2f}, Trades={len(res.trades):2d} | Long: {len(longs)} tr (${long_pnl:.2f}) | Short: {len(shorts)} tr (${short_pnl:.2f})")
