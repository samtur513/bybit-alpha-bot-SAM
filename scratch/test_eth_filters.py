"""Test ETH with stricter volume and momentum filters."""
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

sim_module.BREAKEVEN_ATR_MULT = 999.0

# Test ETH filtering:
# What if for ETH we require volume_ratio >= 2.0 and RSI >= 60 for longs, ADX >= 30 for shorts?
for eth_vol_min in [1.5, 2.0, 2.5]:
    for eth_adx_min in [20, 25, 30]:
        sim = MarketSimulator(strategy_params=param, initial_capital=Decimal("300.0"))
        sim.profile = sim.profile.__class__(**{**sim.profile.__dict__, "tp_atr_mult": 3.0})
        
        orig_gen = sim.strategy.generate_signals_for_bar
        def eth_filtered_gen(row, symbol):
            sig = orig_gen(row, symbol)
            if not sig:
                return None
            if sig.direction == -1 and sig.adx_value < 30:
                return None
            if symbol == "ETHUSDT":
                if sig.volume_ratio < eth_vol_min:
                    return None
                if sig.adx_value < eth_adx_min:
                    return None
            return sig
        sim.strategy.generate_signals_for_bar = eth_filtered_gen
        
        res = sim.run(df_1m_dict, prepared, funding_dict, DEV_START_TS, FINAL_END_TS)
        m = compute_metrics(res)
        eth_t = [t for t in res.trades if t.symbol == "ETHUSDT"]
        eth_pnl = sum(float(t.net_pnl) for t in eth_t)
        print(f"ETH vol>={eth_vol_min:.1f}, adx>={eth_adx_min} -> Total: ${m['net_profit']:.2f} (PF {m['profit_factor']:.2f}, DD {m['max_drawdown_pct']:.1f}%) | ETH: {len(eth_t)} tr, PnL=${eth_pnl:.2f}")
