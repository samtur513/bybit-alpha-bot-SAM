"""Test ETH with ADX >= 30 across all 3 periods."""
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

sim_module.BREAKEVEN_ATR_MULT = 999.0

print("=== ETH ADX >= 30 Across 3 Independent Periods ===")
total_pnl = 0.0

for period_name, start_ts, end_ts in [
    ("Development", DEV_START_TS, DEV_END_TS),
    ("Validation", VAL_START_TS, VAL_END_TS),
    ("Final Test", FINAL_START_TS, FINAL_END_TS),
]:
    sim = MarketSimulator(strategy_params=param, initial_capital=Decimal("300.0"))
    sim.profile = sim.profile.__class__(**{**sim.profile.__dict__, "tp_atr_mult": 3.0})
    
    orig_gen = sim.strategy.generate_signals_for_bar
    def optimized_gen(row, symbol):
        sig = orig_gen(row, symbol)
        if not sig:
            return None
        # Shorts require ADX >= 30 across all symbols
        if sig.direction == -1 and sig.adx_value < 30:
            return None
        # ETH requires volume >= 2.0x and ADX >= 30
        if symbol == "ETHUSDT":
            if sig.volume_ratio < 2.0 or sig.adx_value < 30:
                return None
        return sig
    sim.strategy.generate_signals_for_bar = optimized_gen
    
    res = sim.run(df_1m_dict, prepared, funding_dict, start_ts, end_ts, PERIOD_ENTRY_CUTOFF_MS)
    m = compute_metrics(res)
    total_pnl += m["net_profit"]
    
    btc_t = [t for t in res.trades if t.symbol == "BTCUSDT"]
    eth_t = [t for t in res.trades if t.symbol == "ETHUSDT"]
    btc_pnl = sum(float(t.net_pnl) for t in btc_t)
    eth_pnl = sum(float(t.net_pnl) for t in eth_t)
    
    print(f"[{period_name:11s}] NetPnL=${m['net_profit']:6.2f} ({m['return_pct']:5.1f}%), PF={m['profit_factor']:4.2f}, MaxDD={m['max_drawdown_pct']:4.1f}%, Trades={m['total_trades']:2d}, WR={m['win_rate']:4.1f}%")
    print(f"   BTC: {len(btc_t)} tr, PnL=${btc_pnl:.2f} | ETH: {len(eth_t)} tr, PnL=${eth_pnl:.2f}")

print(f"\n-> Sum of 3 independent periods: ${total_pnl:.2f}")
