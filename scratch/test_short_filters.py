"""Test stricter short filters and asymmetric risk."""
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

sim_module.BREAKEVEN_ATR_MULT = 999.0  # No BE

# Test 1: What if we only take Longs?
print("=== Test 1: LONG ONLY ===")
total_long_pnl = 0.0
for period_name, start_ts, end_ts in [
    ("Development", DEV_START_TS, DEV_END_TS),
    ("Validation", VAL_START_TS, VAL_END_TS),
    ("Final Test", FINAL_START_TS, FINAL_END_TS),
]:
    sim = MarketSimulator(strategy_params=param, initial_capital=Decimal("300.0"))
    sim.profile = sim.profile.__class__(**{**sim.profile.__dict__, "tp_atr_mult": 3.0})
    
    # Filter candidate signals to only Long
    original_generate = sim.strategy.generate_signals_for_bar
    def long_only_signals(row, symbol):
        sig = original_generate(row, symbol)
        if sig and sig.direction == -1:
            return None
        return sig
    sim.strategy.generate_signals_for_bar = long_only_signals
    
    res = sim.run(df_1m_dict, prepared, funding_dict, start_ts, end_ts, PERIOD_ENTRY_CUTOFF_MS)
    m = compute_metrics(res)
    total_long_pnl += m["net_profit"]
    print(f"[{period_name:11s}] PnL=${m['net_profit']:6.2f} ({m['return_pct']:5.1f}%), PF={m['profit_factor']:4.2f}, MaxDD={m['max_drawdown_pct']:4.1f}%, Trades={m['total_trades']:2d}, WR={m['win_rate']:4.1f}%")
print(f"-> 2-Year Long-Only Total: ${total_long_pnl:.2f}")

# Test 2: What if Shorts require stronger ADX (ADX >= 25 or 30)?
for adx_short_min in [25, 30]:
    print(f"\n=== Test 2: Shorts requiring ADX >= {adx_short_min} ===")
    total_pnl = 0.0
    for period_name, start_ts, end_ts in [
        ("Development", DEV_START_TS, DEV_END_TS),
        ("Validation", VAL_START_TS, VAL_END_TS),
        ("Final Test", FINAL_START_TS, FINAL_END_TS),
    ]:
        sim = MarketSimulator(strategy_params=param, initial_capital=Decimal("300.0"))
        sim.profile = sim.profile.__class__(**{**sim.profile.__dict__, "tp_atr_mult": 3.0})
        
        orig_gen = sim.strategy.generate_signals_for_bar
        def strict_short_signals(row, symbol):
            sig = orig_gen(row, symbol)
            if sig and sig.direction == -1:
                if sig.adx_value < adx_short_min:
                    return None
            return sig
        sim.strategy.generate_signals_for_bar = strict_short_signals
        
        res = sim.run(df_1m_dict, prepared, funding_dict, start_ts, end_ts, PERIOD_ENTRY_CUTOFF_MS)
        m = compute_metrics(res)
        total_pnl += m["net_profit"]
        print(f"[{period_name:11s}] PnL=${m['net_profit']:6.2f} ({m['return_pct']:5.1f}%), PF={m['profit_factor']:4.2f}, MaxDD={m['max_drawdown_pct']:4.1f}%, Trades={m['total_trades']:2d}, WR={m['win_rate']:4.1f}%")
    print(f"-> 2-Year Total (ADX>={adx_short_min}): ${total_pnl:.2f}")
