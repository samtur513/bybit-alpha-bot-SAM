"""
Main CLI entry point for Bybit Research & Automation System.
Implements:
  python main.py download
  python main.py audit
  python main.py research
"""
import argparse
import io
import logging
import sys
from decimal import Decimal
from pathlib import Path
import pandas as pd

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from src.config import (
    PARQUET_DATA_DIR,
    PARAMETER_GRID,
    REPORTS_DIR,
    SYMBOLS,
    StrategyParams,
)
from src.data_downloader import DataDownloader
from src.data_auditor import DataAuditor
from src.strategy import StrategyV01
from src.research_protocol import ResearchProtocol
from src.reporter import Reporter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ]
)
logger = logging.getLogger("Main")

def cmd_download(args):
    """Downloads all historical data according to SPEC_RU.md."""
    logger.info("Starting historical data download...")
    downloader = DataDownloader(max_workers=args.workers)
    downloader.build_all_datasets()
    logger.info("Download completed successfully!")

def cmd_audit(args):
    """Audits data quality and integrity."""
    logger.info("Auditing downloaded datasets...")
    auditor = DataAuditor()
    all_valid = True
    for sym in SYMBOLS:
        rep = auditor.audit_1m_dataset(sym)
        if not rep.is_valid:
            all_valid = False
            logger.warning(f"Data audit FAILED for {sym}!")
        else:
            logger.info(f"Data audit PASSED for {sym}!")
    return all_valid

def cmd_research(args):
    """Executes the complete 2-year research protocol and generates report."""
    logger.info("Loading Parquet datasets...")
    df_1m_dict = {}
    funding_dict = {}
    
    for sym in SYMBOLS:
        p_1m = PARQUET_DATA_DIR / f"{sym}_1m.parquet"
        p_fund = PARQUET_DATA_DIR / f"{sym}_funding.parquet"
        if not p_1m.exists():
            logger.error(f"Dataset {p_1m} not found! Please run `python main.py download` first.")
            sys.exit(1)
        df_1m_dict[sym] = pd.read_parquet(p_1m)
        funding_dict[sym] = pd.read_parquet(p_fund) if p_fund.exists() else pd.DataFrame()

    logger.info("Preparing 1h and 4h closed candles with indicators for all parameter sets...")
    downloader = DataDownloader()
    df_1h_dict = {}
    df_4h_dict = {}
    for sym in SYMBOLS:
        df_1h_dict[sym] = downloader.aggregate_candles(df_1m_dict[sym], 1)
        df_4h_dict[sym] = downloader.aggregate_candles(df_1m_dict[sym], 4)

    df_1h_prepared_by_params = {}
    cached_prepared_by_n = {}
    for p in PARAMETER_GRID:
        if p.N not in cached_prepared_by_n:
            strat = StrategyV01(params=p)
            cached_prepared_by_n[p.N] = {}
            for sym in SYMBOLS:
                cached_prepared_by_n[p.N][sym] = strat.prepare_data(
                    df_1m_dict[sym], df_1h_dict[sym], df_4h_dict[sym]
                )
        df_1h_prepared_by_params[p] = cached_prepared_by_n[p.N]

    logger.info("Launching Research Protocol...")
    protocol = ResearchProtocol(
        df_1m_dict=df_1m_dict,
        df_1h_prepared_dict_by_params=df_1h_prepared_by_params,
        funding_dict=funding_dict,
    )
    results = protocol.run_protocol()

    logger.info("Generating Final Research Report...")
    reporter = Reporter()
    report_text = reporter.generate_markdown_report(results)
    
    report_path = REPORTS_DIR / "RESEARCH_REPORT.md"
    logger.info(f"Report written to {report_path}")
    print("\n" + "="*80)
    print(report_text)
    print("="*80 + "\n")

def cmd_research_v2(args):
    """Executes the v2.0 Multi-Alpha research protocol with Kelly sizing and Pyramiding."""
    logger.info("Executing v2.0 Multi-Alpha research protocol...")
    from src.research_v2_runner import run_v2_research, generate_v2_report
    snap, eng = run_v2_research()
    rep = generate_v2_report(snap, eng)
    report_file = REPORTS_DIR / "RESEARCH_REPORT_V2.md"
    report_file.write_text(rep, encoding="utf-8")
    logger.info(f"Report written to {report_file}")
    print("\n" + "="*80)
    print(rep)
    print("="*80 + "\n")

def cmd_paper_trading(args):
    """Starts live Paper Trading daemon on Bybit V5 market data."""
    from src.paper_trader_v2 import PaperTraderV2
    symbols = args.symbols.split(",") if args.symbols else ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    logger.info(f"Initializing Bybit Paper Trading daemon for: {symbols}")
    trader = PaperTraderV2(
        symbols=symbols,
        initial_capital=Decimal(str(args.capital)),
        poll_interval_seconds=args.interval,
    )
    trader.run_daemon()

def cmd_dashboard(args):
    """Starts live Web Dashboard and Paper Trading server on http://localhost:8080."""
    from web_dashboard import start_server_and_bot
    start_server_and_bot()

def main():
    parser = argparse.ArgumentParser(description="Bybit Quantitative Research & Trading System")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # download
    p_dl = subparsers.add_parser("download", help="Download official historical data")
    p_dl.add_argument("--workers", type=int, default=12, help="Concurrent workers")

    # audit
    p_audit = subparsers.add_parser("audit", help="Audit dataset quality")

    # research (v1.0 baseline)
    p_res = subparsers.add_parser("research", help="Execute complete 2-year research protocol (v1.0)")

    # research-v2 (v2.0 Multi-Alpha Growth System)
    p_res2 = subparsers.add_parser("research-v2", help="Execute v2.0 Multi-Alpha (Kelly Sizing + Pyramiding)")

    # paper-trading (Live Bybit Simulation Console)
    p_paper = subparsers.add_parser("paper-trading", help="Start real-time Paper Trading daemon on Bybit V5 (console)")
    p_paper.add_argument("--symbols", type=str, default="BTCUSDT,ETHUSDT,SOLUSDT", help="Comma-separated symbols")
    p_paper.add_argument("--capital", type=float, default=300.0, help="Initial virtual capital (USDT)")
    p_paper.add_argument("--interval", type=int, default=10, help="Poll interval in seconds")

    # dashboard (Live Web Dashboard)
    p_dash = subparsers.add_parser("dashboard", help="Start interactive live Web Dashboard at http://localhost:8080")

    args = parser.parse_args()
    if args.command == "download":
        cmd_download(args)
    elif args.command == "audit":
        cmd_audit(args)
    elif args.command == "research":
        cmd_research(args)
    elif args.command == "research-v2":
        cmd_research_v2(args)
    elif args.command == "paper-trading":
        cmd_paper_trading(args)
    elif args.command == "dashboard":
        cmd_dashboard(args)

if __name__ == "__main__":
    main()


