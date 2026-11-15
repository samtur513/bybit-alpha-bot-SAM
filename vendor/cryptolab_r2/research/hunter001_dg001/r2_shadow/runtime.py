"""Independent paper runtime; frozen observer/strategy code is imported unchanged."""
from pathlib import Path
import shutil, argparse, calendar, datetime, hashlib, json, os, sqlite3, sys, time, uuid
from collections import deque
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, HTTPRedirectHandler, ProxyHandler, build_opener
from raw_store import Archive

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
BASE = "815d6884980791309cb37770ac96a9a93a2c48c0"
UTC = datetime.timezone.utc
FROZEN_BLOBS = {'research/hunter001_dg001/r2_validation/core.py': '04e37595f0609603edcf14afaf97ad87fb781b57', 'research/hunter001_dg001/r2_validation/execution.py': '354ef3cc2b1949ee11ded5c03e0944fb6ecf537e', 'research/hunter001_dg001/r2_validation/observer.py': '57b7583b984409a115198dc2c4e661b60be1fff0', 'research/hunter001_dg001/r2_validation/public_data.py': 'b9d88603c828ed85f588255c8d50b62b538981d0', 'research/hunter001_dg001/r2_validation/PREREGISTRATION.json': 'dd5f94e8af24bec7f1f5d4ceceaacf2ef2f5bb45', 'research/hunter001_dg001/r2_validation/runtime_requirements.txt': '083e41a6459270310234798f084da0b81a781889', 'research/hunter001_dg001/t01_profit_rank/profit_rank.py': '4391da82ab26aa3cb849a504efa7fd5f702f3f7a', 'research/hunter001_dg001/t01_profit_rank/PREREGISTRATION.json': 'c03233e9bafba73b04d31f5c13d955d885d7a15d', 'research/hunter001_dg001/r2_validation/DEPLOYMENT_STATUS.json': '5526dec81f495cb5f9b7c7907277795f2d030bb1'}

def verify_frozen():
    manifest = json.loads((HERE / "FROZEN_MANIFEST.json").read_text(encoding="utf-8"))
    if manifest["source_commit"] != BASE:
        raise RuntimeError("WRONG_SOURCE_COMMIT")
    if {k:v["git_blob_sha"] for k,v in manifest["files"].items()} != FROZEN_BLOBS:
        raise RuntimeError("FROZEN_MANIFEST_SET_CHANGED")
    for name, record in manifest["files"].items():
        body = (REPO / name).read_bytes()
        git = hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()
        if git != record["git_blob_sha"] or hashlib.sha256(body).hexdigest() != record["sha256"]:
            raise RuntimeError("FROZEN_SOURCE_CHANGED: " + name)
    return manifest

def frozen_modules():
    if sys.flags.optimize:
        raise RuntimeError("OPTIMIZED_PYTHON_FORBIDDEN")
    verify_frozen()
    origin = HERE.parent / "r2_validation"
    sys.path.insert(0, str(origin))
    import observer
    import numpy
    if numpy.__version__ != "2.5.4" or sys.version_info[:2] != (3,12):
        raise RuntimeError("RUNTIME_VERSION_MISMATCH")
    for name in ("observer", "core", "execution", "public_data"):
        if Path(sys.modules[name].__file__).resolve().parent != origin:
            raise RuntimeError("IMPORT_ISOLATION_FAILED")
    return observer

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("HTTP_REDIRECT_FORBIDDEN")

class CompactPublic:
    def __init__(self, folder):
        self.archive = Archive(folder)
        self.receipts = deque(maxlen=100)
        self.opener = build_opener(ProxyHandler({}), NoRedirect())

    def get(self, path, **params):
        ob = frozen_modules()
        public = sys.modules["public_data"]
        if path not in public.ALLOWED:
            raise ValueError("PUBLIC_ENDPOINT_ALLOWLIST")
        if path != "/v5/market/time" and (params.get("category") != "spot" or params.get("symbol") not in public.SYMS):
            raise ValueError("SPOT_SYMBOL_ALLOWLIST")
        if path == "/v5/market/kline":
            public.guard_kline(int(params["start"]), int(params["end"]))
        self.archive.preflight()
        url = "https://api.bybit.com" + path + "?" + urlencode(params)
        started = datetime.datetime.now(UTC)
        mono = time.monotonic()
        request = Request(url, headers={"User-Agent": "CryptoLab-R2-SHADOW/1.0", "Cache-Control": "no-cache"}, method="GET")
        try:
            response = self.opener.open(request, timeout=12)
        except HTTPError as error:
            response = error
        with response:
            receipt = dict(id=uuid.uuid4().hex, url=url, started_at=started.isoformat(),
                           available_at=started.isoformat(), ingested_at=started.isoformat(),
                           _monotonic_started=mono,
                           available_kind="OBSERVED_HTTP_RECEIPT_NOT_HISTORICAL_PUBLISH_TIME",
                           http_status=response.status, response_headers=list(response.headers.items()))
            receipt = self.archive.capture(response, receipt)
        self.receipts.append(receipt)
        if receipt["bytes"] > 1_000_000:
            raise ValueError("RESPONSE_CAP_FULL_BODY_ARCHIVED")
        body, _ = self.archive.read(receipt["file"])
        obj = json.loads(body)
        if receipt["http_status"] != 200 or obj.get("retCode") != 0:
            raise ValueError("PUBLIC_API_ERROR_BODY_ARCHIVED")
        # These semantic fields are derived; the complete original body remains primary.
        returned = dict(receipt, server_at_ms=obj.get("time"), retCode=obj.get("retCode"))
        return obj, returned

def add_months(start, months=6):
    month = start.month - 1 + months
    year, month = start.year + month // 12, month % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return start.replace(year=year, month=month, day=day)

def validate_cohort(cohort, now=None):
    now = now or datetime.datetime.now(UTC)
    if cohort.get("status") != "PLANNED" or cohort.get("initial_capital_usdt") != 300:
        raise ValueError("INVALID_COHORT")
    start = datetime.datetime.fromisoformat(cohort["t0_utc"].replace("Z", "+00:00"))
    if start.utcoffset() != datetime.timedelta(0) or start.time() != datetime.time(0):
        raise ValueError("T0_MUST_BE_UTC_MIDNIGHT")
    if start <= now or start < datetime.datetime(2026, 11, 15, tzinfo=UTC):
        raise ValueError("T0_MUST_BE_FUTURE")
    expected = ["TORG_V32_NATIVE_NEW_COHORT", "TORG_V32_REALISTIC_SHADOW", "CRYPTOLAB_R2_SHADOW"]
    if cohort.get("participants") != expected or cohort.get("minimum_end_utc") != add_months(start).isoformat():
        raise ValueError("COHORT_HORIZON_OR_PARTICIPANTS")
    return start

def prepare(folder, cohort, now=None):
    validate_cohort(cohort, now)
    frozen_modules()
    root = Path(folder).resolve()
    root.mkdir(parents=True, exist_ok=False)
    ob = frozen_modules()
    ob.atomic(root / "COHORT.json", cohort)
    ob.atomic(root / "IDENTITY.json", dict(component="CRYPTOLAB_R2_SHADOW", source_commit=BASE,
                                         deployment_authorized=False, capital_reset_allowed=False))
    store = ob.Store(root)
    store.db.execute("PRAGMA synchronous=FULL")
    store.db.close()
    return root

def recover(folder):
    """Read-only validation: all 24 accounts and deterministic execution chains."""
    root = Path(folder).resolve()
    identity = json.loads((root / "IDENTITY.json").read_text())
    if identity.get("component") != "CRYPTOLAB_R2_SHADOW" or identity.get("source_commit") != BASE:
        raise RuntimeError("RUNTIME_IDENTITY_MISMATCH")
    db = sqlite3.connect((root / "observer.sqlite").as_uri() + "?mode=ro", uri=True)
    try:
        db.execute("BEGIN")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("LEDGER_CORRUPT")
        rows = db.execute("SELECT id,payload FROM accounts ORDER BY id").fetchall()
        expected = {f"{s}:{c}:{k}:{p}" for s in ["BASELINE", "R2"] for c in [300,1000,10000]
                    for k in ["BASE","STRESS"] for p in ["MODEL_C5","OBSERVED_BOOK"]}
        if {k for k, _ in rows} != expected:
            raise RuntimeError("PROFILE_MATRIX_CHANGED")
        balances = {k: dict(cash=float(json.loads(v)["capital"]), qty=[0.,0.,0.]) for k,v in rows}
        count = 0
        for eid, payload in db.execute("SELECT id,payload FROM events WHERE kind='ORDER' ORDER BY substr(id,1,20),rowid"):
            event = json.loads(payload)
            key = f"{event['strategy']}:{event['capital']}:{event['cost']}:{event['policy']}"
            account = balances[key]
            j = ["BTCUSDT", "ETHUSDT", "SOLUSDT"].index(event["symbol"])
            if abs(account["cash"] - event["cash_before"]) > 1e-6 or abs(account["qty"][j] - event["qty_before"]) > 1e-8:
                raise RuntimeError("LEDGER_CHAIN_BROKEN")
            if abs(event["cash_before"] + event["cash_delta"] - event["cash_after"]) > 1e-6:
                raise RuntimeError("LEDGER_CASH_MISMATCH")
            expected_qty = event["qty_before"] + (event["gross_qty"] - event["fee_base"] if event["side"] == "BUY" else -event["gross_qty"])
            if abs(expected_qty - event["qty_after"]) > 1e-8 or event["cash_after"] < -1e-8 or event["qty_after"] < -1e-10:
                raise RuntimeError("LEDGER_QUANTITY_MISMATCH")
            account["cash"], account["qty"][j] = event["cash_after"], event["qty_after"]
            count += 1
        for key, payload in rows:
            a, b = json.loads(payload), balances[key]
            if abs(a["cash"] - b["cash"]) > 1e-6 or any(abs(x-y)>1e-8 for x,y in zip(a["qty"],b["qty"])):
                raise RuntimeError("CHECKPOINT_LEDGER_MISMATCH")
        missing = db.execute("SELECT count(*) FROM decisions d WHERE NOT EXISTS (SELECT 1 FROM events e WHERE e.id='DECISION:'||d.day AND e.kind='DECISION')").fetchone()[0]
        if missing:
            raise RuntimeError("DECISION_CHECKPOINT_MISMATCH")
        raw_references = []
        for (payload,) in db.execute("SELECT payload FROM events WHERE kind='SNAPSHOT'"):
            s = json.loads(payload)
            raw_references.extend(s[k] for k in ["book_sha256", "metadata_sha256", "candle_sha256"] if k in s)
    finally:
        db.close()
    archive = Archive(root / "raw")
    archive.recover()
    hashes = set()
    for p in archive.root.glob("raw-*.sqlite"):
        conn = sqlite3.connect(p.resolve().as_uri()+"?mode=ro",uri=True)
        try:
            for (v,) in conn.execute("SELECT receipt FROM raw"):
                hashes.add(json.loads(v)["sha256"])
        finally:
            conn.close()
    if any(h not in hashes for h in raw_references):
        raise RuntimeError("LEDGER_RAW_REFERENCE_MISSING")
    return dict(status="PASS", profiles=24, order_events=count, raw_references=len(raw_references))

def make_observer(folder):
    ob = frozen_modules()
    instance = ob.Observer.__new__(ob.Observer)
    instance.store = ob.Store(folder)
    instance.store.db.execute("PRAGMA synchronous=FULL")
    instance.api = CompactPublic(Path(folder) / "raw")
    instance.meta_cache, instance.last_risk, instance.last_export = {}, 0., 0.
    from marks import QuoteCache
    instance.quote_cache = QuoteCache()
    original_markets = instance.markets
    def capture_quotes(*args, **kwargs):
        markets = original_markets(*args, **kwargs)
        try:
            instance.quote_cache.observe(markets, ob.utc())
        except (KeyError, TypeError, ValueError, OverflowError):
            # Telemetry failure must not alter any frozen decision/execution behavior.
            instance.quote_cache.clear()
        return markets
    instance.markets = capture_quotes
    return instance

def resource_gate(root):
    files = [p for p in root.rglob("*") if p.is_file()]
    if len(files)>3000 or sum(p.stat().st_size for p in files)>10*1024**3 or shutil.disk_usage(root).free<512*1024**2:
        raise RuntimeError("RUNTIME_RESOURCE_GATE")

def run(folder, policy_path):
    # Refuse before any directory, lock, DB or HTTP side effects.
    policy = json.loads(Path(policy_path).read_text())
    if policy.get("deployment_authorized") is not True or policy.get("vps_gate_passed") is not True:
        raise RuntimeError("DEPLOYMENT_NOT_AUTHORIZED")
    frozen_modules()
    root = Path(folder).resolve()
    cohort = json.loads((root / "COHORT.json").read_text())
    if not all(policy.get(k) is True for k in ["three_participants_ready", "telemetry_contract_accepted", "local_verification_passed"]):
        raise RuntimeError("INTEGRATION_GATE_PENDING")
    start = datetime.datetime.fromisoformat(cohort["t0_utc"].replace("Z", "+00:00"))
    validate_cohort(cohort, now=start-datetime.timedelta(seconds=1))
    if policy.get("authorized_start_utc") != cohort["t0_utc"]:
        raise RuntimeError("AUTHORIZED_T0_MISMATCH")
    if not (root / "START_RECEIPT.json").exists():
        validate_cohort(cohort)
    from readiness import validate
    ready = validate(root, policy, cohort)
    resource_gate(root)
    ob = frozen_modules()
    with ob.process_lock(root):
        recover(root)
        instance = make_observer(root)
        receipt = root / "START_RECEIPT.json"
        if receipt.exists():
            previous = json.loads(receipt.read_text())
            if previous.get("readiness", {}).get("code_sha256") != ready["code_sha256"]:
                raise RuntimeError("READINESS_START_CODE_CHANGED")
            if previous["cohort"] != cohort:
                raise RuntimeError("COHORT_CHANGED")
        else:
            ob.atomic(receipt, dict(cohort=cohort, status="ARMED", process_started_at=ob.utc().isoformat(), source_commit=BASE, readiness=ready))
        frozen = {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in HERE.glob("*.py")}
        bound = json.dumps(cohort, sort_keys=True)
        while not (root / "STOP").exists():
            current = json.loads(Path(policy_path).read_text())
            if current != policy or json.dumps(json.loads((root/"COHORT.json").read_text()),sort_keys=True) != bound:
                raise RuntimeError("AUTHORIZATION_OR_COHORT_CHANGED")
            verify_frozen()
            if any(hashlib.sha256(p.read_bytes()).hexdigest()!=h for p,h in frozen.items()):
                raise RuntimeError("SHADOW_CODE_CHANGED")
            # Frozen slots and no-backfill rules are delegated unchanged.
            resource_gate(root)
            from readiness import check_resources, resource_sample
            check_resources(resource_sample(root))
            instance.tick(start.isoformat())
            ob.atomic(root / "HEARTBEAT.json", dict(at=ob.utc().isoformat(), status="ARMED" if ob.utc()<start else "OBSERVING"))
            from telemetry import export
            export(root, root / "telemetry.json", quotes=instance.quote_cache)
            time.sleep(15)
        instance.store.db.close()

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["prepare","recover","run"])
    p.add_argument("--data-dir", required=True)
    p.add_argument("--cohort", default=str(HERE/"COHORT_PLAN.json"))
    p.add_argument("--policy", default=str(HERE/"AUTHORIZATION.json"))
    a = p.parse_args()
    if a.mode == "prepare":
        print(prepare(a.data_dir, json.loads(Path(a.cohort).read_text())))
    elif a.mode == "recover":
        print(json.dumps(recover(a.data_dir)))
    else:
        run(a.data_dir, a.policy)
