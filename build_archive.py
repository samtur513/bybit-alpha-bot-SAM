"""
Creates standalone deployment archives (.tar.gz and .zip) for Bybit Multi-Alpha Bot v2.0.
Excludes git histories, virtual environments, cache directories, and transient logs.
"""

import os
from pathlib import Path
import tarfile
import zipfile

BASE_DIR = Path(__file__).resolve().parent

INCLUDED_FILES = [
    "web_dashboard.py",
    "setup.sh",
    "reset_test.sh",
    "start.sh",
    "stop.sh",
    "restart.sh",
    "status.sh",
    "logs.sh",
    "requirements.txt",
    "README_DEPLOY.md",
    "README.md",
    "SPEC_V2.md",
    "SYSTEM_ARCHITECTURE_V2.md",
    ".gitignore",
]

INCLUDED_DIRS = [
    "src",
    "tests",
]

OUTPUT_TAR = BASE_DIR / "bybit_alpha_bot_v2.tar.gz"
OUTPUT_ZIP = BASE_DIR / "bybit_alpha_bot_v2.zip"

def get_all_target_files():
    files_to_pack = []
    
    # Root files
    for fname in INCLUDED_FILES:
        fpath = BASE_DIR / fname
        if fpath.exists():
            files_to_pack.append((fpath, fname))
            
    # Subdirectories
    for dname in INCLUDED_DIRS:
        dpath = BASE_DIR / dname
        for root, dirs, files in os.walk(dpath):
            dirs[:] = [d for d in dirs if d != "__pycache__" and d != ".pytest_cache"]
            for f in files:
                if f.endswith(".pyc") or f.endswith(".pyo"):
                    continue
                full_path = Path(root) / f
                rel_path = full_path.relative_to(BASE_DIR).as_posix()
                files_to_pack.append((full_path, rel_path))

    # Clean state template
    template_state = BASE_DIR / "reports" / "BOT_STATE_PERSISTENT.json"
    if template_state.exists():
        files_to_pack.append((template_state, "reports/BOT_STATE_PERSISTENT.json"))

    return files_to_pack

def create_tar_gz(files):
    print(f"Creating {OUTPUT_TAR.name}...")
    with tarfile.open(OUTPUT_TAR, "w:gz") as tar:
        for full_path, arcname in files:
            tar.add(full_path, arcname=arcname)
    print(f"[OK] {OUTPUT_TAR.name} created! Size: {OUTPUT_TAR.stat().st_size / 1024:.1f} KB")

def create_zip(files):
    print(f"Creating {OUTPUT_ZIP.name}...")
    with zipfile.ZipFile(OUTPUT_ZIP, "w", zipfile.ZIP_DEFLATED) as z:
        for full_path, arcname in files:
            z.write(full_path, arcname=arcname)
    print(f"[OK] {OUTPUT_ZIP.name} created! Size: {OUTPUT_ZIP.stat().st_size / 1024:.1f} KB")

if __name__ == "__main__":
    targets = get_all_target_files()
    print(f"Total files to package: {len(targets)}")
    create_tar_gz(targets)
    create_zip(targets)
    print("Done packaging complete standalone distribution.")
