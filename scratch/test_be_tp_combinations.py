"""Test different breakeven and trailing parameters."""
import sys
sys.path.insert(0, ".")
from decimal import Decimal
import pandas as pd

from src.config import (
    DEV_START_TS,
    DEV_END_TS,
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

# Test different BREAKEVEN_ATR_MULT and TP settings
print("Testing different Breakeven and TP configurations on Dev:")
for be_mult in [1.5, 2.0, 999.0]:  # 999.0 means no breakeven stop
    for tp_mult in [2.5, 3.0, 4.0]:
        sim_module.BREAKEVEN_ATR_MULT = be_mult
        
        sim = MarketSimulator(strategy_params=param, initial_capital=Decimal("300.0"))
        sim.profile = sim.profile.__class__(
            **{**sim.profile.__dict__, "tp_atr_mult": tp_mult}
        )
        res = sim.run(df_1m_dict, prepared, funding_dict, DEV_START_TS, DEV_END_TS, PERIOD_ENTRY_CUTOFF_MS)
        m = compute_metrics(res)
        be_str = "No BE" if be_mult == 999.0 else f"BE={be_mult:.1f}"
        print(f"{be_str:7s}, TP={tp_mult:.1f}x -> Trades={m['total_trades']:3d}, NetPnL=${m['net_profit']:6.2f}, Ret={m['return_pct']:5.1f}%, MaxDD={m['max_drawdown_pct']:4.1f}%, PF={m['profit_factor']:4.2f}, WR={m['win_rate']:4.1f}%, AvgWin=${m['avg_win']:4.2f}, AvgLoss=${m['avg_loss']:5.2f}")
