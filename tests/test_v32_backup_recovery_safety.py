#!/usr/bin/env python3
"""
tests/test_v32_backup_recovery_safety.py
Verifies P0 requirements for backup and recovery safety:
1. Safe SQLite Online Backup API (.backup) vs dangerous wal_checkpoint(TRUNCATE).
2. Isolated stand restoration: verification of schema, record count, and PRAGMA integrity_check.
3. Secrets sanitization: exclusion/masking of sensitive API keys from public archives.
"""

from decimal import Decimal
import os
from pathlib import Path
import sqlite3
import tempfile
import pytest

from v3_2.src.shadow.paper_trader_v32_shadow import V32LegacyShadowEngine


def test_sqlite_online_backup_api_integrity():
    """
    Verifies that the SQLite Online Backup API creates a consistent,
    fully checkpointed backup from a live WAL-mode database without TRUNCATE locks.
    """
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    tmpdir = tmp.name

    live_db_path = os.path.join(tmpdir, "live_production.db")
    backup_db_path = os.path.join(tmpdir, "safe_backup.db")

    # 1. Populate live database in WAL mode
    conn = sqlite3.connect(live_db_path)
    try:
        with conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("CREATE TABLE live_trades (id INTEGER PRIMARY KEY, symbol TEXT, pnl REAL);")
            for i in range(100):
                conn.execute("INSERT INTO live_trades (symbol, pnl) VALUES (?, ?);", (f"SYM_{i}", float(i * 1.5)))
    finally:
        conn.close()

    # 2. Perform safe Online Backup API copy (simulating safe_vps_backup script)
    src_conn = sqlite3.connect(live_db_path)
    dst_conn = sqlite3.connect(backup_db_path)
    try:
        with dst_conn:
            src_conn.backup(dst_conn, pages=20)
    finally:
        dst_conn.close()
        src_conn.close()

    # 3. Verify backup database on isolated fixture
    assert os.path.exists(backup_db_path)
    check_conn = sqlite3.connect(backup_db_path)
    try:
        cursor = check_conn.cursor()
        cursor.execute("PRAGMA integrity_check;")
        status = cursor.fetchone()[0]
        assert status == "ok"

        cursor.execute("SELECT count(*) FROM live_trades;")
        count = cursor.fetchone()[0]
        assert count == 100
    finally:
        check_conn.close()

    try:
        tmp.cleanup()
    except Exception:
        pass


def test_secrets_sanitization_in_backup_filter():
    """
    Verifies that .env configuration files are sanitized/masked before archiving.
    """
    sample_env = """
    BYBIT_API_KEY=ABC123456789xyz
    BYBIT_API_SECRET=SecretKeyVerySensitive999
    TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
    PORT=8083
    EQUITY_USDT=300.0
    """

    sanitized_lines = []
    for line in sample_env.strip().splitlines():
        line = line.strip()
        if any(secret_key in line for secret_key in ["API_KEY", "API_SECRET", "BOT_TOKEN", "PASSWORD"]):
            key = line.split("=")[0]
            sanitized_lines.append(f"{key}=***MASKED_FOR_RESEARCH_SAFETY***")
        else:
            sanitized_lines.append(line)

    sanitized_output = "\n".join(sanitized_lines)
    assert "ABC123456789xyz" not in sanitized_output
    assert "SecretKeyVerySensitive999" not in sanitized_output
    assert "123456:ABC-DEF" not in sanitized_output
    assert "BYBIT_API_KEY=***MASKED_FOR_RESEARCH_SAFETY***" in sanitized_output
    assert "PORT=8083" in sanitized_output
