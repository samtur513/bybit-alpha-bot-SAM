"""Explicitly approved R2 recovery: CPU load advisory, six other limits fatal."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
R2 = ROOT / "vendor/cryptolab_r2/research/hunter001_dg001/r2_shadow"
READINESS = R2 / "readiness.py"

spec = importlib.util.spec_from_file_location("readiness_cpu_advisory_test", READINESS)
readiness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(readiness)

GIB = 1024 ** 3
MIB = 1024 ** 2


def nominal():
    return {
        "free_disk_bytes": 20 * GIB,
        "mem_available_bytes": 1024 * MIB,
        "free_inodes": 50_000,
        "fd_soft_limit": 65535,
        "load_per_cpu": 0.2,
        "runtime_files": 20,
        "runtime_bytes": 4 * MIB,
    }


def test_cpu_load_is_not_a_fatal_gate_even_under_parallel_workloads():
    for load in (0.8, 0.891113, 1.5, 5.0, 16.0):
        x = nominal()
        x["load_per_cpu"] = load
        assert readiness.check_resources(x) == x


@pytest.mark.parametrize("key,value", [
    ("free_disk_bytes", 0),
    ("mem_available_bytes", 512*MIB - 1),
    ("free_inodes", 4095),
    ("fd_soft_limit", 1023),
    ("runtime_files", 3001),
    ("runtime_bytes", 10*GIB+1),
])
def test_six_other_resource_guards_remain_fatal(key, value):
    sample = nominal()
    sample[key] = value
    with pytest.raises(RuntimeError, match="READINESS_RESOURCE_LIMITS"):
        readiness.check_resources(sample)


def test_resource_values_still_need_finite_nonnegative_load():
    for load in (-1.0, float("inf"), float("nan")):
        sample = nominal()
        sample["load_per_cpu"] = load
        with pytest.raises(RuntimeError, match="READINESS_RESOURCE_VALUES"):
            readiness.check_resources(sample)


def test_recovery_approval_matches_exact_source_sha():
    patch = json.loads((R2 / "RECOVERY_PATCH.json").read_text())
    assert patch["protocol"] == "R2-RECOVERY-001"
    approved = patch["approved_patch"]["readiness.py"]
    observed = hashlib.sha256(READINESS.read_bytes()).hexdigest()
    assert approved == observed
    # Frozen observer/runtime identity is not modified by this recovery.
    assert "runtime.py" in patch["approved_patch"]
