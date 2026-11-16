"""Verify R2 external diagnostic reproduces frozen resource thresholds."""
from tools.r2_resource_observer import (
    HALF_GIB, TEN_GIB, check_against_frozen_thresholds,
)


def nominal_sample():
    return {
        "free_disk_bytes": 20 * 1024 ** 3,
        "mem_available_bytes": 1 * 1024 ** 3,
        "free_inodes": 50_000,
        "fd_soft_limit": 4096,
        "load_per_cpu": 0.4,
        "runtime_files": 500,
        "runtime_bytes": 50 * 1024 ** 2,
    }


def test_nominal_r2_resource_limits_pass():
    result = check_against_frozen_thresholds(nominal_sample())
    assert result["would_fail_readiness"] is False
    assert result["triggered_rules"] == []


def test_specific_load_limit_tells_exact_reason():
    s = nominal_sample()
    s["load_per_cpu"] = 0.80001
    out = check_against_frozen_thresholds(s)
    assert out["triggered_rules"] == ["LOAD_PER_CPU_GT_0_8"]
    s["load_per_cpu"] = 0.8
    assert check_against_frozen_thresholds(s)["would_fail_readiness"] is False


def test_memory_limit():
    s = nominal_sample()
    s["mem_available_bytes"] = HALF_GIB - 1
    assert check_against_frozen_thresholds(s)["triggered_rules"] == [
        "MEM_AVAILABLE_LT_512MIB"
    ]


def test_disk_headroom_includes_10gib_runtime_reserve():
    s = nominal_sample()
    minimum = max(0, TEN_GIB - s["runtime_bytes"]) + HALF_GIB
    s["free_disk_bytes"] = minimum
    assert not check_against_frozen_thresholds(s)["would_fail_readiness"]
    s["free_disk_bytes"] = minimum - 1
    out = check_against_frozen_thresholds(s)
    assert "FREE_DISK_BELOW_RESERVED_HEADROOM" in out["triggered_rules"]
    assert out["disk_required_bytes"] == minimum


def test_inode_fd_runtime_count_and_bytes():
    cases = [
        ("free_inodes", 4095, "FREE_INODES_LT_4096"),
        ("fd_soft_limit", 1023, "FD_SOFT_LIMIT_LT_1024"),
        ("runtime_files", 3001, "RUNTIME_FILES_GT_3000"),
        ("runtime_bytes", TEN_GIB + 1, "RUNTIME_BYTES_GT_10GIB"),
    ]
    for field, value, expected_rule in cases:
        s = nominal_sample()
        s[field] = value
        assert expected_rule in check_against_frozen_thresholds(s)["triggered_rules"]


def test_invalid_sample_rejected():
    import pytest
    s = nominal_sample()
    del s["load_per_cpu"]
    with pytest.raises(ValueError):
        check_against_frozen_thresholds(s)
    s = nominal_sample()
    s["runtime_files"] = float("nan")
    with pytest.raises(ValueError):
        check_against_frozen_thresholds(s)
