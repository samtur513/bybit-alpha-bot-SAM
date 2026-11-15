"""
v3_2.src.runners
Standalone operational execution runners for TORG-BOT Three-System Comparative Cohort:
- run_native_paper: Native v3.2 Original Paper Trader (CoreV3 Donchian Trend, 0.090% MNT tier, isolated ledger)
- run_shadow_paper: Realistic Shadow v3.2 Paper Trader (V32LegacyShadowEngine, L2 depth walking, VIP0 tier)
- run_three_system_dashboard: Unified read-only comparative dashboard daemon (Port 8099)

Note: CryptoLab R2-SHADOW is run directly via CryptoLab's genuine runtime (313a908782271a6e1fffaee83be7ea1a616337cb).
TORG-BOT operates strictly as a read-only consumer of CryptoLab's authentic telemetry.json export.
"""
