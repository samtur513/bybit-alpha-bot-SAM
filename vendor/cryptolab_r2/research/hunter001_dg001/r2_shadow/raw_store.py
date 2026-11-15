"""Lossless compressed raw segments. No retention deletion; quota failures stop input."""
from pathlib import Path
import time, datetime, gzip, hashlib, io, json, os, shutil, sqlite3, threading, uuid
UTC = datetime.timezone.utc

def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()

class Archive:
    def __init__(self, root, quota=8*1024**3, reserve=512*1024**2, daily=256*1024**2):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.quota, self.reserve, self.daily = quota, reserve, daily
        self.lock = threading.RLock()

    def preflight(self, extra=0):
        files = list(self.root.iterdir())
        used = sum(p.stat().st_size for p in files if p.is_file())
        if used + extra + 131072 > self.quota or shutil.disk_usage(self.root).free < self.reserve + extra:
            raise RuntimeError("RAW_RESOURCE_GATE")
        if len(files) > 2000:
            raise RuntimeError("RAW_INODE_GATE")

    def capture(self, response, receipt):
        """Stream the entire body to a durable spool before parsing/accepting it."""
        with self.lock:
            self.preflight()
        rid = receipt["id"]
        if not (len(rid) == 32 and all(c in "0123456789abcdef" for c in rid)):
            raise ValueError("INVALID_RECEIPT_ID")
        tmp = self.root / ("capture-" + rid + ".partial")
        complete = self.root / ("capture-" + rid + ".gz")
        sha = hashlib.sha256()
        size = 0
        try:
            with tmp.open("xb") as f:
                with gzip.GzipFile(filename="", fileobj=f, mode="wb", mtime=0) as out:
                    while True:
                        b = response.read(65536)
                        if not b:
                            break
                        with self.lock:
                            self.preflight(len(b))
                        out.write(b)
                        sha.update(b)
                        size += len(b)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, complete)
            receipt = dict(receipt)
            mono = receipt.pop("_monotonic_started", time.monotonic())
            finished = datetime.datetime.now(UTC).isoformat()
            receipt.update(available_at=finished, ingested_at=finished, rtt_ms=(time.monotonic()-mono)*1000, bytes=size, sha256=sha.hexdigest(), complete=True)
            pending = complete.with_suffix(".json")
            with pending.open("x", encoding="utf-8") as f:
                json.dump(receipt, f, allow_nan=False)
                f.flush()
                os.fsync(f.fileno())
            result = self.pack(complete, receipt)
            pending.unlink()
            complete.unlink()
            return result
        except BaseException:
            # Interrupted/incomplete bytes are retained and block clean recovery.
            raise

    def _path(self, receipt):
        stamp = datetime.datetime.fromisoformat(receipt["available_at"])
        if stamp.utcoffset() != datetime.timedelta(0):
            raise ValueError("RAW_TIME_NOT_UTC")
        return self.root / ("raw-" + stamp.date().isoformat() + ".sqlite")

    def pack(self, source, receipt):
        source = Path(source)
        with self.lock:
            self.preflight(source.stat().st_size)
            target = self._path(receipt)
            if target.with_suffix(".sha256").exists():
                raise RuntimeError("SEALED_RAW_SEGMENT")
            if (target.stat().st_size if target.exists() else 0) + source.stat().st_size + 131072 > self.daily:
                raise RuntimeError("RAW_DAILY_GATE")
            encoded_sha = digest_file(source)
            # Verify decompressed original before accepting a complete receipt.
            h = hashlib.sha256()
            n = 0
            with gzip.open(source, "rb") as f:
                for b in iter(lambda: f.read(65536), b""):
                    h.update(b)
                    n += len(b)
            if h.hexdigest() != receipt["sha256"] or n != receipt["bytes"]:
                raise ValueError("RAW_HASH_MISMATCH")
            result = dict(receipt, file=target.name + "#" + receipt["id"])
            encoded = json.dumps(result, sort_keys=True, allow_nan=False)
            db = sqlite3.connect(target)
            try:
                db.execute("PRAGMA synchronous=FULL")
                db.executescript("""
CREATE TABLE IF NOT EXISTS raw(id TEXT UNIQUE NOT NULL, receipt TEXT NOT NULL, gzip_sha256 TEXT NOT NULL, body BLOB NOT NULL);
CREATE TRIGGER IF NOT EXISTS raw_no_update BEFORE UPDATE ON raw BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
CREATE TRIGGER IF NOT EXISTS raw_no_delete BEFORE DELETE ON raw BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END;
""")
                old = db.execute("SELECT receipt,gzip_sha256 FROM raw WHERE id=?", (receipt["id"],)).fetchone()
                if old:
                    if old[0] != encoded:
                        raise ValueError("RAW_ID_CONFLICT")
                    self.read(result["file"])
                else:
                    with db:
                        cur = db.execute("INSERT INTO raw VALUES (?,?,?,zeroblob(?))",
                                         (receipt["id"], encoded, encoded_sha, source.stat().st_size))
                        with db.blobopen("raw", "body", cur.lastrowid) as blob, source.open("rb") as f:
                            for b in iter(lambda: f.read(65536), b""):
                                blob.write(b)
            finally:
                db.close()
            return result

    def read(self, locator):
        name, rid = locator.split("#")
        if Path(name).name != name or not name.startswith("raw-") or not name.endswith(".sqlite"):
            raise ValueError("INVALID_RAW_LOCATOR")
        db = sqlite3.connect((self.root / name).resolve().as_uri() + "?mode=ro", uri=True)
        try:
            row = db.execute("SELECT receipt,gzip_sha256,body FROM raw WHERE id=?", (rid,)).fetchone()
        finally:
            db.close()
        if not row:
            raise KeyError(locator)
        receipt, encoded_sha, body = row
        if hashlib.sha256(body).hexdigest() != encoded_sha:
            raise ValueError("COMPRESSED_HASH_MISMATCH")
        b = gzip.decompress(body)
        receipt = json.loads(receipt)
        if len(b) != receipt["bytes"] or hashlib.sha256(b).hexdigest() != receipt["sha256"]:
            raise ValueError("RAW_HASH_MISMATCH")
        return b, receipt

    def recover(self):
        with self.lock:
            for manifest in sorted(self.root.glob("capture-*.json")):
                receipt = json.loads(manifest.read_text(encoding="utf-8"))
                source = manifest.with_suffix(".gz")
                self.pack(source, receipt)
                manifest.unlink()
                source.unlink()
            # Crash after manifest removal but before spool removal: verify committed bytes.
            for source in sorted(self.root.glob("capture-*.gz")):
                rid = source.stem.removeprefix("capture-")
                matched = False
                for segment in self.root.glob("raw-*.sqlite"):
                    db = sqlite3.connect(segment.resolve().as_uri()+"?mode=ro", uri=True)
                    try:
                        row = db.execute("SELECT gzip_sha256 FROM raw WHERE id=?", (rid,)).fetchone()
                    finally:
                        db.close()
                    if row:
                        if row[0] != digest_file(source):
                            raise ValueError("ORPHAN_SPOOL_HASH_MISMATCH")
                        self.read(segment.name+"#"+rid)
                        source.unlink()
                        matched = True
                        break
                if not matched:
                    raise RuntimeError("ORPHAN_RAW_SPOOL_REQUIRES_AUDIT")
            pending = list(self.root.glob("capture-*"))
            if pending:
                raise RuntimeError("INCOMPLETE_RAW_CAPTURE_REQUIRES_AUDIT")
            for path in self.root.glob("raw-*.sqlite"):
                db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
                try:
                    if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("RAW_DATABASE_CORRUPT")
                    ids = [r[0] for r in db.execute("SELECT id FROM raw ORDER BY rowid")]
                finally:
                    db.close()
                seal = path.with_suffix(".sha256")
                if seal.exists() and seal.read_text().split()[0] != digest_file(path):
                    raise ValueError("SEGMENT_HASH_MISMATCH")
                for rid in ids:
                    self.read(path.name + "#" + rid)

    def seal(self, day):
        path = self.root / ("raw-" + datetime.date.fromisoformat(day).isoformat() + ".sqlite")
        if datetime.date.fromisoformat(day) >= datetime.datetime.now(UTC).date():
            raise ValueError("CANNOT_SEAL_ACTIVE_DAY")
        with self.lock:
            self.recover()
            seal = path.with_suffix(".sha256")
            if seal.exists():
                return seal.read_text().split()[0]
            sha = digest_file(path)
            with seal.open("x", encoding="ascii") as f:
                f.write(sha + "  " + path.name + "\n")
                f.flush()
                os.fsync(f.fileno())
            return sha
