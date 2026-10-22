"""Crash-resistant JSON snapshots; caller serializes access."""
import json
import os
import tempfile
from pathlib import Path


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=2, allow_nan=False)
    fd, name = tempfile.mkstemp(prefix=path.name, suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
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
