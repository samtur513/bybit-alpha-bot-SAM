"""
Configuration for v3.13 Intra-Sector Beta-Neutral Spike Reversion Engine (Port 8080).
Upgraded with Auditor Recommendations:
1. Realistic horizon: 3 to 8 minutes (time stop 300-450s).
2. Adverse selection defense: Enter only after confirmed exhaustion + 20-25% pullback from extreme.
3. 2-Stage Exit: 50% Partial TP at Z <= 0.70 + move remaining 50% stop to Breakeven (+0.05%).
4. Dynamic ATR-scaled spike threshold: max(0.75%, 1.2 * ATR_15m).
5. Max concurrent sector spreads: 2.
6. Maker-Quoting Post-Only on alpha leg + sub-5ms WebSocket auto-hedge on peer leg.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

PORT_8080 = int(os.getenv("PORT_V313", "8080"))
MODULE_ID = "M4_SECTOR_ARB_8080"
INITIAL_CAPITAL_USDT = 300.0

@dataclass
class SectorPairConfig:
    sector_name: str
    symbol_a: str
    symbol_b: str
    base_notional_a: float = 50.0

SECTOR_PAIRS: List[SectorPairConfig] = [
    SectorPairConfig("Layer-1 Ecosystem", "SOLUSDT", "SUIUSDT", base_notional_a=50.0),
    SectorPairConfig("Meme Super-Beta", "1000PEPEUSDT", "WIFUSDT", base_notional_a=40.0),
    SectorPairConfig("AI & Decentralized Compute", "NEARUSDT", "RENDERUSDT", base_notional_a=40.0),
    SectorPairConfig("Layer-2 Rollups", "ARBUSDT", "OPUSDT", base_notional_a=40.0),
]

ALL_MONITORED_SYMBOLS = list(set(
    [p.symbol_a for p in SECTOR_PAIRS] + [p.symbol_b for p in SECTOR_PAIRS] + ["BTCUSDT"]
))

# Fee Model (MNT discount: Maker 0.0324%, Taker 0.090%)
FEE_MAKER = 0.000324
FEE_TAKER = 0.000900
ROUNDTRIP_MAKER_TAKER_FEE = 2.0 * (FEE_MAKER + FEE_TAKER) # 0.002448 = 0.2448%

# Signal & Risk Parameters (Auditor-Hardened)
MIN_Z_ENTRY = 2.20
PULLBACK_REVERSAL_CONFIRM_PCT = 0.25 # 25% confirmed retracement from spike extreme
MIN_SPIKE_DISLOCATION_PCT = 0.0075  # 75 bps absolute minimum (3x fee hurdle)
ATR_MULTIPLIER_SPIKE = 1.20         # Dynamic hurdle based on 15m ATR

# Exit Parameters (2-Stage Smart Exit)
TP1_Z_THRESHOLD = 0.70              # Stage 1: 50% partial TP
TP2_Z_RUNNER_THRESHOLD = 0.15       # Stage 2: full convergence
BE_PROFIT_BUFFER_PCT = 0.0005       # +0.05% fee cover when stop moved to Breakeven
SL_Z_EXPANSION = 3.60               # Hard statistical stop if spread blows out to Z=3.6
MAX_HOLD_SECONDS = 360.0            # 6 minutes time stop (360 seconds)
MAX_CONCURRENT_SPREADS = 2          # Max 2 active sector pairs
MAX_DAILY_LOSS_USDT = 6.0           # $6.00 (2.0%) Circuit Breaker
