# Forward observer — implemented, NOT DEPLOYED

Frozen strategy protocol: 7b6fed5ae5d72573e827f4e5c2cee419c183b445. Current operational authority: DEPLOYMENT_STATUS.json. Implementation and controls are tested; no approved 24/7 deployment exists. The local test process was stopped before its previously planned start. No prospective execution or six-month evidence exists.

24 independent virtual accounts: BASELINE/R2 x $300/$1000/$10000 x BASE/STRESS x MODEL_C5/OBSERVED_BOOK. Both strategies use the same execution function. No trading core, private API, credentials, orders, Docker or notification integration.

After separate permission and publication of a future start, decisions are scheduled at00:00:05 UTC (03:00:05 Nairobi), at most5min late. Collect29 completed daily candles per symbol, require continuous series ending yesterday and freeze signal inputs/weights transactionally. Jan–Sep2026 requests remain prohibited. The archived November15/May15 dates are inactive; no data can be backfilled to simulate deployment.

Execution estimates at01:00 UTC (04:00 Nairobi), max60s late. Fresh Spot metadata and depth50 books for all3 coins, max5s age/skew, ordered positive noncrossed books. Both cost paths retain actual virtual cash/quantity/dust and rejects. Sales precede pro-rata purchases. Current observed precision/min/max/status/depth checks are distinct from C5 modeling and account-specific unknowns. Quote-derived estimates never prove fills.

Risk level1 polling every60s, metadata at least hourly, heartbeat every15s; persist hourly equity and decision/execution marks. Observed MDD>30% permanently disqualifies real capital in that run. Gaps block positive claims; intraminute extremes are unknown. SQLite atomic IDs/transactions, OS process lock, append-only raw receipts, code hash checks and explicit missed slots prevent duplicate or backdated paper accounting. The 100-receipt RAM cache does not truncate disk evidence.

daemon refuses startup before deployment_authorized=true and an exactly matching authorized_start_utc. Missing/invalid policy fails before DB/network initialization. Authorization is an operational guard, not a substitute for actual user permission. Control mode remains a bounded local public-data test and cannot establish deployment or economic validity.

DEPLOYMENT_PLAN.md and deployment/r2-paper.service.example are preparation only. The runtime ZIP contains only9 allowlisted research source/configuration files plus its internal manifest. Nothing is installed on a server by building or publishing it. Linux compatibility, quotas, available resources, clock, canary impact, start/restart and actual heartbeat must be verified after permission. No uptime, autostart, alert delivery or zero-impact promise follows from local tests.

Minimum confirmation remains six full calendar months from an actually deployed, preregistered future start, followed by source/execution/ledger/risk/statistical audits. Historical primary random turnover comparability failed and remains an explicit blocker. No automatic live authorization exists.
