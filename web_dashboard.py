"""
Live Web Dashboard & Trading Engine Server (Port 8080).
v3.13 Intra-Sector Beta-Neutral Spike Reversion Engine.
Decommissions deprecated v2.0 Breakout baseline and launches the auditor-hardened
institutional sector pairs arbitrage engine with Bybit V5 streaming, TCA attribution,
anti-adverse selection pullback confirmation, and 2-stage exits.
"""

import asyncio
import logging
from pathlib import Path
import sys

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from v3_13_sector_arb.src.sector_arb_8080 import SectorArbEngine8080

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [PORT_8080_LAUNCHER] %(message)s",
)
logger = logging.getLogger("Launcher8080")


def main():
    logger.info("Initializing v3.13 Intra-Sector Beta-Neutral Spike Reversion Engine on Port 8080...")
    engine = SectorArbEngine8080()
    try:
        asyncio.run(engine.run())
    except KeyboardInterrupt:
        logger.info("Engine stopped by user.")


if __name__ == "__main__":
    main()
