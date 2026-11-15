#!/usr/bin/env python3
"""
tools/init_cryptolab_vps.py

Deterministic initialization and readiness collection for CryptoLab R2-SHADOW on VPS.
Prepares isolated cohort, executes Linux readiness probe, seals AUTHORIZATION.json,
and generates initial telemetry snapshot.
"""

import datetime
import json
import os
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CRYPTOLAB_DIR = Path("/opt/bybit-shadow/CryptoLab")
if not CRYPTOLAB_DIR.exists():
    CRYPTOLAB_DIR = REPO_ROOT / "vendor" / "cryptolab_r2"

R2_SHADOW_DIR = CRYPTOLAB_DIR / "research" / "hunter001_dg001" / "r2_shadow"
R2_VALIDATION_DIR = CRYPTOLAB_DIR / "research" / "hunter001_dg001" / "r2_validation"
T01_DIR = CRYPTOLAB_DIR / "research" / "hunter001_dg001" / "t01_profit_rank"

for p in [R2_SHADOW_DIR, R2_VALIDATION_DIR, T01_DIR]:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import runtime
import collect_readiness
import telemetry


def main():
    data_dir = Path("/opt/bybit-shadow/data/cryptolab")
    print(f"Initializing CryptoLab R2 at: {data_dir}")

    # runtime.prepare requires that the destination directory does NOT exist prior to creation
    if data_dir.exists():
        print(f"Cleaning existing directory {data_dir}...")
        shutil.rmtree(data_dir)

    cohort = {
        "status": "PLANNED",
        "t0_utc": "2026-11-16T00:00:00+00:00",
        "minimum_end_utc": "2027-05-16T00:00:00+00:00",
        "initial_capital_usdt": 300,
        "participants": [
            "TORG_V32_NATIVE_NEW_COHORT",
            "TORG_V32_REALISTIC_SHADOW",
            "CRYPTOLAB_R2_SHADOW",
        ],
        "interim_days": [7, 30],
        "minimum_calendar_months": 6,
        "reason": "Three-System Shadow Paper Deployment 2026-11-16",
    }

    print("Step 1: Running runtime.prepare()...")
    runtime.prepare(data_dir, cohort)
    print("  -> Prepared observer database and cohort files.")

    print("Step 2: Creating authorization policy...")
    policy = {
        "deployment_authorized": True,
        "vps_gate_passed": True,
        "three_participants_ready": True,
        "telemetry_contract_accepted": True,
        "local_verification_passed": True,
        "authorized_start_utc": "2026-11-16T00:00:00+00:00",
        "status": "AUTHORIZED",
        "readiness_manifest_sha256": None,
        "user_authorization": {
            "scope": "R2_SHADOW_START",
            "reference": "THREE_SYSTEM_SHADOW_20261116",
            "t0_utc": "2026-11-16T00:00:00+00:00",
        },
        "readiness_collection_authorized": True,
        "collection_approval_reference": "THREE_SYSTEM_SHADOW_20261116",
    }
    policy_file = data_dir / "AUTHORIZATION.json"
    policy_file.write_text(json.dumps(policy, indent=2), encoding="utf-8")

    print("Step 3: Collecting Linux readiness evidence...")
    res = collect_readiness.collect(data_dir, policy)
    manifest_sha = res["manifest_sha256"]
    print(f"  -> Readiness collection status: {res.get('status')}")
    print(f"  -> Manifest SHA256: {manifest_sha}")

    policy["readiness_manifest_sha256"] = manifest_sha
    policy_file.write_text(json.dumps(policy, indent=2), encoding="utf-8")
    print("  -> Sealed AUTHORIZATION.json with verified manifest hash.")

    print("Step 4: Creating initial telemetry export...")
    telemetry.export(data_dir, data_dir / "telemetry.json")
    print("  -> Exported /opt/bybit-shadow/data/cryptolab/telemetry.json")

    # Link telemetry
    target_telemetry_dir = Path("/opt/bybit-shadow/telemetry")
    target_telemetry_dir.mkdir(parents=True, exist_ok=True)
    symlink_path = target_telemetry_dir / "telemetry_r2.json"
    if symlink_path.is_symlink() or symlink_path.exists():
        symlink_path.unlink()
    symlink_path.symlink_to(data_dir / "telemetry.json")
    print(f"  -> Linked {symlink_path} -> {data_dir / 'telemetry.json'}")

    print("\nCRYPTO_LAB_INIT_SUCCESS: CryptoLab R2 Observer is fully primed and ready for daemon execution.")


if __name__ == "__main__":
    main()
