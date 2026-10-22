"""Crash-resistant JSON snapshots; caller serializes access."""
import json
import logging
import os
import tempfile
import time
from pathlib import Path

logger = logging.getLogger("persistence")


def atomic_json(path, data, max_retries=8, initial_delay=0.01):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, allow_nan=False)
    fd, name = tempfile.mkstemp(prefix=path.name, suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())

        # On Windows, os.replace can momentarily fail if another process is reading the file.
        # Retry with exponential backoff on PermissionError.
        for attempt in range(max_retries):
            try:
                os.replace(name, path)
                break
            except (PermissionError, OSError) as e:
                # If non-transient OSError or max retries exceeded, propagate exception
                if attempt == max_retries - 1 or not isinstance(e, PermissionError):
                    raise
                time.sleep(initial_delay * (1.5 ** attempt))

        if os.name == 'posix':
            try:
                directory = os.open(str(path.parent), os.O_DIRECTORY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except Exception:
                pass
    finally:
        if os.path.exists(name):
            try:
                os.unlink(name)
            except Exception:
                pass

