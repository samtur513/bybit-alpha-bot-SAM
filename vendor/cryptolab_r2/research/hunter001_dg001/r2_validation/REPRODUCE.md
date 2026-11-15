# Reproduce the frozen local study

Strategy protocol: 7b6fed5ae5d72573e827f4e5c2cee419c183b445. Base:0efacb4b13912c8a7b9da24963ef84e7a472be88. Research branch:research/r2-validation-001-20261114.

**Paper Observer NOT DEPLOYED. No VPS access or install is authorized. The local control process is stopped. These commands reproduce local evidence only.**

~~~powershell
git -c core.autocrlf=false clone --branch research/r2-validation-001-20261114 https://github.com/samtur513/CryptoLab.git CryptoLab-r2-validation
cd CryptoLab-r2-validation/research/hunter001_dg001
python -m pip install -r r2_validation/requirements.txt
python r2_validation/seal.py --verify
python -m pytest -c r2_validation/pytest.ini r2_validation/test_validation.py t01_profit_rank/test_profit_rank.py t01_confirm/test_confirm.py alpha_search/test_alpha.py --basetemp=work/r2_reproduction_tests --junitxml=work/r2_reproduction_tests.xml -q
python r2_validation/audit.py
python r2_validation/make_reports.py
python r2_validation/forward_report.py --data-dir r2_validation/evidence/control_state --output work/r2_control_status
python r2_validation/build_deployment_bundle.py
~~~

Optional identical offline economic reproduction (no new parameter trial):

~~~powershell
python r2_validation/historical.py
~~~

It uses only the published small history NPZ and immutable parent research engine. No keys, accounts, original2026 holdout, local caches or new market requests are required. All6000 random paths and failed primary turnover comparability are retained. Historical results are modeled economics, not proved execution. Verify SHA256SUMS BEFORE rerunning generators; XML and timed resource profiles are operational evidence, not byte-identical timings.

Optional local resource probe against archived real controls (no network):

~~~powershell
$env:OPENBLAS_NUM_THREADS='1'
$env:OMP_NUM_THREADS='1'
python r2_validation/resource_probe.py
~~~

A bounded public control remains available for local technical verification only:

~~~powershell
python r2_validation/observer.py control --data-dir work/r2_manual_control
~~~

It retrieves completed29-day bars and public books/metadata for3coins, labels rows CONTROL_ONLY and does not advance prospective balances. Original Jan–Sep2026 requests are guarded. Do not interpret a control as deployment or performance.

daemon intentionally refuses before separate user permission and a matching published future start. No daemon-launch command is authorized by this document. See DEPLOYMENT_PLAN.md for the inactive service template, exact package contents, Linux dependencies, file hashes, measured local resources, disk/inode forecast, network and rights, and preflight/canary conditions. All future server commands there are preparation only.

Old FORWARD_START_RECEIPT/HEARTBEAT records are immutable historical evidence of the stopped local verification. DEPLOYMENT_STATUS.json, OPERATIONS_RECEIPT.json and evidence/LOCAL_VERIFICATION_STOP.json define current status. The previous Nov15/May15 dates are inactive; obtain permission and publish a future-start operational addendum before an actual six-month study. Never erase old evidence or backfill.

Raw historical responses: history/RAW_RESPONSES.zip. Raw control responses: evidence/CONTROL_RAW_RESPONSES.zip. Receipt rows carry SHA256 of decompressed HTTP bodies; archives have their own manifest hashes. audit.py reads ZIP directly without loose raw files or external caches. The separate minimal runtime ZIP excludes historical prices, controls, keys, services and trading cores.
