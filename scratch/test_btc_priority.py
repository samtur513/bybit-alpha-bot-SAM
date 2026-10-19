"""Test prioritization of BTCUSDT over ETHUSDT."""
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

# Priority test: what if BTCUSDT is prioritized over ETHUSDT?
# In simulator, candidate_signals are sorted. Let's inspect!
print("Testing BTCUSDT priority vs BTC-only vs Both:")

for mode in ["BTC_PRIORITY", "BTC_ONLY", "BOTH_ORIGINAL"]:
    sim = MarketSimulator(strategy_params=param, initial_capital=Decimal("300.0"))
    sim.profile = sim.profile.__class__(**{**sim.profile.__dict__, "tp_atr_mult": 3.0})
    
    orig_gen = sim.strategy.generate_signals_for_bar
    def custom_gen(row, symbol):
        if mode == "BTC_ONLY" and symbol != "BTCUSDT":
            return None
        sig = orig_gen(row, symbol)
        if sig and sig.direction == -1 and sig.adx_value < 30:
            return None
        return sig
    sim.strategy.generate_signals_for_bar = custom_gen
    
    res = sim.run(df_1m_dict, prepared, funding_dict, DEV_START_TS, FINAL_END_TS)
    m = compute_metrics(res)
    
    btc_trades = [t for t in res.trades if t.symbol == "BTCUSDT"]
    eth_trades = [t for t in res.trades if t.symbol == "ETHUSDT"]
    btc_pnl = sum(float(t.net_pnl) for t in btc_trades)
    eth_pnl = sum(float(t.net_pnl) for t in eth_trades)
    
    print(f"\n[{mode}] NetPnL=${m['net_profit']:.2f} ({m['return_pct']:.1f}%), PF={m['profit_factor']:.2f}, MaxDD={m['max_drawdown_pct']:.1f}%, Trades={m['total_trades']}, WR={m['win_rate']:.1f}%")
    print(f"   BTC: {len(btc_trades)} tr, PnL=${btc_pnl:.2f} | ETH: {len(eth_trades)} tr, PnL=${eth_pnl:.2f}")
