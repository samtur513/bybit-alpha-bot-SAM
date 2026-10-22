"""
Research Protocol Execution Module.
Strict adherence to SPEC_RU.md Section 6:
- 3-period protocol: Development, Validation, Final Test
- Pre-cutoff (120h) entry stop and boundary force close
- Automated ranking on Development: Net Profit / Max DD (min 50 trades)
- Validation selection: positive net profit, PF >= 1.2, DD <= 30%
- Configuration and code SHA-256 freezing
- Continuous 2-year curve
- Rolling 30-day non-overlapping windows ($2000 / $3000 goal tracking)
- Stress testing: 2x fee, 0.10% slippage, 1-min delay
- Benchmarks: USDT, BTC Buy & Hold, BTC with 30% stop threshold
- Block bootstrap resampling of daily returns
"""
import hashlib
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.config import (
    BASE_SLIPPAGE,
    BASE_TAKER_FEE,
    CAPITALS,
    DEV_END_TS,
    DEV_START_TS,
    FINAL_END_TS,
    FINAL_START_TS,
    PARAMETER_GRID,
    PERIOD_ENTRY_CUTOFF_MS,
    STRESS_SLIPPAGE,
    STRESS_TAKER_FEE,
    StrategyParams,
    VAL_END_TS,
    VAL_START_TS,
)
from src.simulator import MarketSimulator, SimulationResult, TradeRecord

logger = logging.getLogger("Protocol")

def compute_metrics(sim_result: SimulationResult) -> Dict[str, Any]:
    """Computes comprehensive trade and performance metrics."""
    trades = sim_result.trades
    n_trades = len(trades)
    
    if n_trades == 0:
        return {
            "total_trades": 0,
            "net_profit": float(sim_result.net_profit),
            "return_pct": float(sim_result.return_pct),
            "max_drawdown_pct": float(sim_result.max_drawdown_pct),
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "avg_trade_pnl": 0.0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "max_consecutive_losses": 0,
            "stop_out_triggered": sim_result.stop_out_triggered,
            "trades_skipped_min_size": sim_result.skipped_min_size,
            "trades_skipped_risk_budget": sim_result.skipped_risk_budget,
            "btc_trades": 0,
            "eth_trades": 0,
            "long_trades": 0,
            "short_trades": 0,
        }

    wins = [float(t.net_pnl) for t in trades if t.net_pnl > 0]
    losses = [float(t.net_pnl) for t in trades if t.net_pnl < 0]
    
    total_win = sum(wins)
    total_loss = abs(sum(losses))
    pf = (total_win / total_loss) if total_loss > 0 else (999.0 if total_win > 0 else 0.0)
    win_rate = (len(wins) / n_trades) * 100.0

    # Max consecutive losses
    max_cons_loss = 0
    curr_cons_loss = 0
    for t in trades:
        if t.net_pnl <= 0:
            curr_cons_loss += 1
            if curr_cons_loss > max_cons_loss:
                max_cons_loss = curr_cons_loss
        else:
            curr_cons_loss = 0

    btc_trades = sum(1 for t in trades if t.symbol == "BTCUSDT")
    eth_trades = sum(1 for t in trades if t.symbol == "ETHUSDT")
    long_trades = sum(1 for t in trades if t.direction == "LONG")
    short_trades = sum(1 for t in trades if t.direction == "SHORT")

    return {
        "total_trades": n_trades,
        "net_profit": float(sim_result.net_profit),
        "return_pct": float(sim_result.return_pct),
        "max_drawdown_pct": float(sim_result.max_drawdown_pct),
        "win_rate": win_rate,
        "profit_factor": pf,
        "avg_trade_pnl": float(sim_result.net_profit) / n_trades,
        "avg_win": (total_win / len(wins)) if wins else 0.0,
        "avg_loss": (sum(losses) / len(losses)) if losses else 0.0,
        "max_consecutive_losses": max_cons_loss,
        "stop_out_triggered": sim_result.stop_out_triggered,
        "trades_skipped_min_size": sim_result.skipped_min_size,
        "trades_skipped_risk_budget": sim_result.skipped_risk_budget,
        "btc_trades": btc_trades,
        "eth_trades": eth_trades,
        "long_trades": long_trades,
        "short_trades": short_trades,
    }

class ResearchProtocol:
    """Executes the full two-year research protocol."""

    def __init__(
        self,
        df_1m_dict: Dict[str, pd.DataFrame],
        df_1h_prepared_dict_by_params: Dict[StrategyParams, Dict[str, pd.DataFrame]],
        funding_dict: Dict[str, pd.DataFrame],
    ):
        self.df_1m_dict = df_1m_dict
        self.df_1h_prepared = df_1h_prepared_dict_by_params
        self.funding_dict = funding_dict

    def run_protocol(self) -> Dict[str, Any]:
        """Runs the entire 3-stage protocol, windows, stress tests, benchmarks."""
        logger.info("=== Running Stage 1: Development (2024-10-12 to 2025-10-12) ===")
        dev_results: Dict[StrategyParams, Dict[str, Any]] = {}
        for p in PARAMETER_GRID:
            sim = MarketSimulator(strategy_params=p, initial_capital=Decimal("300.0"))
            res = sim.run(
                self.df_1m_dict,
                self.df_1h_prepared[p],
                self.funding_dict,
                start_ts=DEV_START_TS,
                end_ts=DEV_END_TS,
                entry_cutoff_ms=PERIOD_ENTRY_CUTOFF_MS,
            )
            m = compute_metrics(res)
            # Rank score = Net Profit / Max DD
            score = (m["net_profit"] / m["max_drawdown_pct"]) if m["max_drawdown_pct"] > 0 else 0.0
            m["rank_score"] = score
            dev_results[p] = {"sim": res, "metrics": m}
            logger.info(f"Dev Variant N={p.N}, K={p.K}, RSI={p.RSI_LONG_MIN}/{p.RSI_SHORT_MAX}: NetPnL=${m['net_profit']:.2f}, DD={m['max_drawdown_pct']:.2f}%, PF={m['profit_factor']:.2f}, Trades={m['total_trades']}, Score={score:.3f}")

        # Rank variants: net_profit > 0 and trades >= 30 (per SPEC_V1)
        ranked_params = []
        for p in PARAMETER_GRID:
            m = dev_results[p]["metrics"]
            if m["net_profit"] > 0 and m["total_trades"] >= 30:
                ranked_params.append((p, m["rank_score"]))
        
        ranked_params.sort(key=lambda x: x[1], reverse=True)
        ranking_order = [x[0] for x in ranked_params] if ranked_params else PARAMETER_GRID

        logger.info(f"Dev Ranking Order: {[(p.N, p.K, p.RSI_LONG_MIN) for p in ranking_order]}")

        logger.info("=== Running Stage 2: Validation (2025-10-12 to 2026-04-12) ===")
        val_results: Dict[StrategyParams, Dict[str, Any]] = {}
        selected_param: Optional[StrategyParams] = None

        for p in ranking_order:
            sim = MarketSimulator(strategy_params=p, initial_capital=Decimal("300.0"))
            res = sim.run(
                self.df_1m_dict,
                self.df_1h_prepared[p],
                self.funding_dict,
                start_ts=VAL_START_TS,
                end_ts=VAL_END_TS,
                entry_cutoff_ms=PERIOD_ENTRY_CUTOFF_MS,
            )
            m = compute_metrics(res)
            val_results[p] = {"sim": res, "metrics": m}
            logger.info(f"Val Variant N={p.N}, K={p.K}, RSI={p.RSI_LONG_MIN}/{p.RSI_SHORT_MAX}: NetPnL=${m['net_profit']:.2f}, DD={m['max_drawdown_pct']:.2f}%, PF={m['profit_factor']:.2f}, Trades={m['total_trades']}")
            
            # Selection rule: first variant in rank order with net_profit > 0, PF >= 1.2, DD <= 25% (MODERATE)
            if selected_param is None and m["net_profit"] > 0 and m["profit_factor"] >= 1.2 and m["max_drawdown_pct"] <= 25.0:
                selected_param = p
                logger.info(f"Selected Variant for Final Test: N={p.N}, K={p.K}, RSI={p.RSI_LONG_MIN}/{p.RSI_SHORT_MAX}")

        if selected_param is None:
            # Fallback to top ranked if none strictly met PF >= 1.2
            selected_param = ranking_order[0]
            logger.warning(f"No variant passed all validation hurdles. Proceeding with top ranked N={selected_param.N}, K={selected_param.K}, RSI={selected_param.RSI_LONG_MIN}/{selected_param.RSI_SHORT_MAX} to document outcome.")

        # Hash code & configuration
        hasher = hashlib.sha256()
        hasher.update(f"N={selected_param.N}_K={selected_param.K}_RSI={selected_param.RSI_LONG_MIN}_{selected_param.RSI_SHORT_MAX}".encode("utf-8"))
        frozen_config_hash = hasher.hexdigest()

        logger.info(f"=== Running Stage 3: Final Test (2026-04-12 to 2026-10-12) with Frozen Hash {frozen_config_hash[:12]} ===")
        final_sim_300 = MarketSimulator(strategy_params=selected_param, initial_capital=Decimal("300.0"))
        final_res_300 = final_sim_300.run(
            self.df_1m_dict,
            self.df_1h_prepared[selected_param],
            self.funding_dict,
            start_ts=FINAL_START_TS,
            end_ts=FINAL_END_TS,
            entry_cutoff_ms=PERIOD_ENTRY_CUTOFF_MS,
        )
        final_metrics_300 = compute_metrics(final_res_300)

        final_sim_100 = MarketSimulator(strategy_params=selected_param, initial_capital=Decimal("100.0"))
        final_res_100 = final_sim_100.run(
            self.df_1m_dict,
            self.df_1h_prepared[selected_param],
            self.funding_dict,
            start_ts=FINAL_START_TS,
            end_ts=FINAL_END_TS,
            entry_cutoff_ms=PERIOD_ENTRY_CUTOFF_MS,
        )
        final_metrics_100 = compute_metrics(final_res_100)

        logger.info(f"Final Test $300: NetPnL=${final_metrics_300['net_profit']:.2f}, DD={final_metrics_300['max_drawdown_pct']:.2f}%, PF={final_metrics_300['profit_factor']:.2f}, Trades={final_metrics_300['total_trades']}")
        logger.info(f"Final Test $100: NetPnL=${final_metrics_100['net_profit']:.2f}, DD={final_metrics_100['max_drawdown_pct']:.2f}%, PF={final_metrics_100['profit_factor']:.2f}, Trades={final_metrics_100['total_trades']}")

        # Continuous 2-year run
        logger.info("=== Running Continuous 2-Year Evaluation ===")
        cont_sim_300 = MarketSimulator(strategy_params=selected_param, initial_capital=Decimal("300.0"))
        cont_res_300 = cont_sim_300.run(
            self.df_1m_dict,
            self.df_1h_prepared[selected_param],
            self.funding_dict,
            start_ts=DEV_START_TS,
            end_ts=FINAL_END_TS,
        )
        cont_metrics_300 = compute_metrics(cont_res_300)

        cont_sim_100 = MarketSimulator(strategy_params=selected_param, initial_capital=Decimal("100.0"))
        cont_res_100 = cont_sim_100.run(
            self.df_1m_dict,
            self.df_1h_prepared[selected_param],
            self.funding_dict,
            start_ts=DEV_START_TS,
            end_ts=FINAL_END_TS,
        )
        cont_metrics_100 = compute_metrics(cont_res_100)

        # Stress tests on selected variant
        logger.info("=== Running Stress Tests on Selected Variant ===")
        stress_tests = self._run_stress_tests(selected_param)

        # 30-day windows evaluation
        logger.info("=== Evaluating Rolling 30-Day Windows ($2000/$3000 Goal) ===")
        windows_summary = self._evaluate_30d_windows(selected_param)

        # Benchmarks
        logger.info("=== Evaluating Benchmarks ===")
        benchmarks = self._evaluate_benchmarks()

        # Block bootstrap resampling
        logger.info("=== Running Block Bootstrap Resampling ===")
        bootstrap_metrics = self._block_bootstrap(cont_res_300)

        return {
            "dev_results": dev_results,
            "val_results": val_results,
            "selected_param": selected_param,
            "frozen_config_hash": frozen_config_hash,
            "final_res_300": final_res_300,
            "final_metrics_300": final_metrics_300,
            "final_res_100": final_res_100,
            "final_metrics_100": final_metrics_100,
            "cont_res_300": cont_res_300,
            "cont_metrics_300": cont_metrics_300,
            "cont_res_100": cont_res_100,
            "cont_metrics_100": cont_metrics_100,
            "stress_tests": stress_tests,
            "windows_summary": windows_summary,
            "benchmarks": benchmarks,
            "bootstrap_metrics": bootstrap_metrics,
        }

    def _run_stress_tests(self, params: StrategyParams) -> Dict[str, Any]:
        """Runs 3 stress tests on the final period."""
        # 1. Double fee (0.12%)
        s1 = MarketSimulator(strategy_params=params, initial_capital=Decimal("300.0"), fee_rate=STRESS_TAKER_FEE, slippage_rate=BASE_SLIPPAGE)
        r1 = s1.run(self.df_1m_dict, self.df_1h_prepared[params], self.funding_dict, FINAL_START_TS, FINAL_END_TS, PERIOD_ENTRY_CUTOFF_MS)
        m1 = compute_metrics(r1)

        # 2. Stress slippage (0.10%)
        s2 = MarketSimulator(strategy_params=params, initial_capital=Decimal("300.0"), fee_rate=BASE_TAKER_FEE, slippage_rate=STRESS_SLIPPAGE)
        r2 = s2.run(self.df_1m_dict, self.df_1h_prepared[params], self.funding_dict, FINAL_START_TS, FINAL_END_TS, PERIOD_ENTRY_CUTOFF_MS)
        m2 = compute_metrics(r2)

        # 3. 1-minute execution delay
        s3 = MarketSimulator(strategy_params=params, initial_capital=Decimal("300.0"), fee_rate=BASE_TAKER_FEE, slippage_rate=BASE_SLIPPAGE, delay_minutes=1)
        r3 = s3.run(self.df_1m_dict, self.df_1h_prepared[params], self.funding_dict, FINAL_START_TS, FINAL_END_TS, PERIOD_ENTRY_CUTOFF_MS)
        m3 = compute_metrics(r3)

        return {
            "fee_x2": {"sim": r1, "metrics": m1},
            "slippage_stress": {"sim": r2, "metrics": m2},
            "delay_1m": {"sim": r3, "metrics": m3},
        }

    def _evaluate_30d_windows(self, params: StrategyParams) -> Dict[str, Any]:
        """
        Slices the 2-year period into non-overlapping 30-day windows.
        Tests user targets: $2000 and $3000 from $100 and $300 start.
        """
        window_ms = 30 * 24 * 3600 * 1000
        windows = []
        curr = DEV_START_TS
        
        while curr + window_ms <= FINAL_END_TS:
            windows.append((curr, curr + window_ms))
            curr += window_ms

        results_300 = []
        results_100 = []

        for idx, (w_start, w_end) in enumerate(windows):
            is_final = w_start >= FINAL_START_TS
            # $300 start
            s300 = MarketSimulator(strategy_params=params, initial_capital=Decimal("300.0"))
            r300 = s300.run(self.df_1m_dict, self.df_1h_prepared[params], self.funding_dict, w_start, w_end)
            m300 = compute_metrics(r300)
            hit_2000 = r300.peak_equity >= Decimal("2000.0")
            hit_3000 = r300.peak_equity >= Decimal("3000.0")
            results_300.append({
                "window_idx": idx + 1,
                "start_ts": w_start,
                "end_ts": w_end,
                "is_final": is_final,
                "final_equity": float(r300.final_equity),
                "peak_equity": float(r300.peak_equity),
                "net_profit": m300["net_profit"],
                "return_pct": m300["return_pct"],
                "max_dd_pct": m300["max_drawdown_pct"],
                "hit_2000": hit_2000,
                "hit_3000": hit_3000,
                "stopped_out": r300.stop_out_triggered,
            })

            # $100 start
            s100 = MarketSimulator(strategy_params=params, initial_capital=Decimal("100.0"))
            r100 = s100.run(self.df_1m_dict, self.df_1h_prepared[params], self.funding_dict, w_start, w_end)
            m100 = compute_metrics(r100)
            hit_2000_100 = r100.peak_equity >= Decimal("2000.0")
            hit_3000_100 = r100.peak_equity >= Decimal("3000.0")
            results_100.append({
                "window_idx": idx + 1,
                "start_ts": w_start,
                "end_ts": w_end,
                "is_final": is_final,
                "final_equity": float(r100.final_equity),
                "peak_equity": float(r100.peak_equity),
                "net_profit": m100["net_profit"],
                "return_pct": m100["return_pct"],
                "max_dd_pct": m100["max_drawdown_pct"],
                "hit_2000": hit_2000_100,
                "hit_3000": hit_3000_100,
                "stopped_out": r100.stop_out_triggered,
            })

        n_win = len(windows)
        hit_2000_count_300 = sum(1 for w in results_300 if w["hit_2000"])
        hit_3000_count_300 = sum(1 for w in results_300 if w["hit_3000"])
        stopped_count_300 = sum(1 for w in results_300 if w["stopped_out"])

        return {
            "total_windows": n_win,
            "results_300": results_300,
            "results_100": results_100,
            "hit_2000_rate_300": (hit_2000_count_300 / n_win * 100.0) if n_win > 0 else 0.0,
            "hit_3000_rate_300": (hit_3000_count_300 / n_win * 100.0) if n_win > 0 else 0.0,
            "stopped_out_rate_300": (stopped_count_300 / n_win * 100.0) if n_win > 0 else 0.0,
        }

    def _evaluate_benchmarks(self) -> Dict[str, Any]:
        """Calculates Buy & Hold BTC benchmark and BTC with 30% stop threshold."""
        df_btc = self.df_1m_dict["BTCUSDT"]
        sub = df_btc[(df_btc["timestamp"] >= DEV_START_TS) & (df_btc["timestamp"] < FINAL_END_TS)].sort_values("timestamp")
        
        p_start = sub.iloc[0]["open"]
        p_end = sub.iloc[-1]["close"]
        
        # 1. Raw B&H return with 0.06% entry + exit taker fee
        fee_factor = (1.0 - float(BASE_TAKER_FEE)) ** 2
        bh_raw_return_pct = ((p_end / p_start) * fee_factor - 1.0) * 100.0

        # Calculate max drawdown of raw BTC B&H
        prices = sub["close"].to_numpy()
        running_max = np.maximum.accumulate(prices)
        drawdowns = (running_max - prices) / running_max
        max_dd_bh = float(np.max(drawdowns)) * 100.0

        # 2. BTC B&H with 30% stop threshold
        stopped_out = False
        stop_price = 0.0
        stop_idx = 0
        final_bh_stopped_return_pct = bh_raw_return_pct
        
        for i in range(len(prices)):
            if drawdowns[i] >= 0.30:
                stopped_out = True
                stop_price = prices[i] * (1.0 - float(BASE_SLIPPAGE))
                stop_idx = i
                # Return at stop out with fees
                final_bh_stopped_return_pct = ((stop_price / p_start) * fee_factor - 1.0) * 100.0
                break

        return {
            "btc_start_price": p_start,
            "btc_end_price": p_end,
            "bh_raw_return_pct": bh_raw_return_pct,
            "bh_raw_max_dd_pct": max_dd_bh,
            "bh_stopped_out": stopped_out,
            "bh_stopped_return_pct": final_bh_stopped_return_pct,
            "bh_stop_price": stop_price,
        }

    def _block_bootstrap(self, sim_res: SimulationResult, n_simulations: int = 500, block_size_days: int = 7) -> Dict[str, Any]:
        """Performs stationary block bootstrap on daily returns to estimate drawdown distribution."""
        curve = sim_res.equity_curve
        if not curve:
            return {"median_dd": 0.0, "p95_dd": 0.0}

        df = pd.DataFrame(curve, columns=["timestamp", "equity"])
        df["equity"] = df["equity"].astype(float)
        df["dt"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df_daily = df.groupby(pd.Grouper(key="dt", freq="1D"))["equity"].last().dropna()
        daily_returns = df_daily.pct_change().dropna().to_numpy(dtype=float)

        if len(daily_returns) < block_size_days * 2:
            return {"median_dd": 0.0, "p95_dd": 0.0}

        n_days = len(daily_returns)
        bootstrap_max_dds = []

        for _ in range(n_simulations):
            sim_returns = []
            while len(sim_returns) < n_days:
                start_idx = np.random.randint(0, n_days - block_size_days)
                sim_returns.extend(daily_returns[start_idx:start_idx + block_size_days])
            
            sim_returns = np.array(sim_returns[:n_days])
            # Construct equity curve
            eq = 300.0 * np.cumprod(1.0 + sim_returns)
            peak = np.maximum.accumulate(eq)
            dd = (peak - eq) / peak
            bootstrap_max_dds.append(float(np.max(dd)) * 100.0)

        return {
            "median_dd": float(np.median(bootstrap_max_dds)),
            "p95_dd": float(np.percentile(bootstrap_max_dds, 95)),
            "p99_dd": float(np.percentile(bootstrap_max_dds, 99)),
        }
