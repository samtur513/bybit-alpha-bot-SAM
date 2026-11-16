#!/usr/bin/env python3
"""External read-only resource observer for frozen CryptoLab R2.

Does NOT import/alter frozen R2 code or touch observer.sqlite, cohort, T0,
trading, API keys, systemd or the protected readiness manifest. Observes the
same numeric seven resource metrics as readiness.check_resources(). Emits
one JSON object per sample, optionally to an independent append-only JSONL log.

This is a diagnostic, not a bypass for RESOURCE_LIMITS. The historical
2026-11-16 outage trigger is UNKNOWN unless captured by a previous sample.
"""
import argparse
import datetime
import json
import math
import os
from pathlib import Path
import resource
import shutil
import sys
import time


TEN_GIB = 10 * 1024 ** 3
HALF_GIB = 512 * 1024 ** 2


def check_against_frozen_thresholds(sample):
    """Read-only reimplementation of readiness.py's seven resource predicates."""
    required = {
        "free_disk_bytes", "mem_available_bytes", "free_inodes",
        "fd_soft_limit", "load_per_cpu", "runtime_files", "runtime_bytes",
    }
    if set(sample) != required:
        raise ValueError("required resource fields missing")
    if any(type(value) not in (int, float) or value < 0 or not math.isfinite(value)
           for value in sample.values()):
        raise ValueError("invalid resource value")
    needed = max(0, TEN_GIB - sample["runtime_bytes"]) + HALF_GIB
    rules = {
        "FREE_DISK_BELOW_RESERVED_HEADROOM": sample["free_disk_bytes"] < needed,
        "MEM_AVAILABLE_LT_512MIB": sample["mem_available_bytes"] < HALF_GIB,
        "FREE_INODES_LT_4096": sample["free_inodes"] < 4096,
        "FD_SOFT_LIMIT_LT_1024": sample["fd_soft_limit"] < 1024,
        "LOAD_PER_CPU_GT_0_8": sample["load_per_cpu"] > 0.8,
        "RUNTIME_FILES_GT_3000": sample["runtime_files"] > 3000,
        "RUNTIME_BYTES_GT_10GIB": sample["runtime_bytes"] > TEN_GIB,
    }
    return {
        "would_fail_readiness": any(rules.values()),
        "triggered_rules": [name for name, triggered in rules.items() if triggered],
        "disk_required_bytes": needed,
        "rules": rules,
    }


def sample_metrics(root):
    """Read host metrics without opening any database or writing to data root."""
    root = Path(root).resolve(strict=True)
    mem = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        key, _, value = line.partition(":")
        if value and value.strip():
            mem[key] = int(value.split()[0]) * 1024
    if "MemAvailable" not in mem:
        raise ValueError("MemAvailable missing")
    disk = shutil.disk_usage(root)
    stat = os.statvfs(root)
    fd_limit = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    if fd_limit < 0:
        fd_limit = 2**63 - 1
    # Match the *exact* file counting contract of the frozen readiness guard.
    count = 0
    total_bytes = 0
    for item in root.rglob("*"):
        if item.is_file():
            count += 1
            total_bytes += item.stat().st_size
    return {
        "free_disk_bytes": int(disk.free),
        "mem_available_bytes": int(mem["MemAvailable"]),
        "free_inodes": int(stat.f_favail),
        "fd_soft_limit": int(fd_limit),
        "load_per_cpu": round(os.getloadavg()[0] / (os.cpu_count() or 1), 6),
        "runtime_files": count,
        "runtime_bytes": total_bytes,
    }


def sample_report(root):
    metrics = sample_metrics(root)
    evaluation = check_against_frozen_thresholds(metrics)
    return {
        "recorded_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "root": str(Path(root).resolve()),
        "diagnostic_only": True,
        "metrics": metrics,
        "evaluation": evaluation,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only R2 RESOURCE_LIMITS metrics observer")
    parser.add_argument("--root", required=True, help="existing frozen R2 runtime data directory")
    parser.add_argument("--watch", action="store_true", help="sample repeatedly without touching R2")
    parser.add_argument("--interval-sec", type=int, default=15)
    parser.add_argument("--log", type=Path, default=None,
                        help="optional independent JSONL log (outside runtime data directory)")
    args = parser.parse_args(argv)
    if args.interval_sec < 10:
        parser.error("sampling more often than 10 seconds is prohibited")
    root = Path(args.root).resolve(strict=True)
    if args.log and (args.log.resolve() == root or root in args.log.resolve().parents):
        parser.error("log must not be inside the frozen R2 data directory")

    while True:
        try:
            record = sample_report(root)
            line = json.dumps(record, separators=(",", ":"), allow_nan=False)
        except (OSError, ValueError) as exc:
            line = json.dumps({
                "recorded_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "diagnostic_only": True,
                "sample_error": type(exc).__name__,
            })
        if args.log:
            # Atomic append of a short line; no writes to frozen research data.
            with args.log.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")
        print(line, flush=True)
        if not args.watch:
            break
        time.sleep(args.interval_sec)
    return 0


if __name__ == "__main__":
    sys.exit(main())
