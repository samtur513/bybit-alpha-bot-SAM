"""Evidence-based launch checks. Loading evidence is read-only; absent evidence rejects launch."""
from pathlib import Path
import datetime, hashlib, json, math, os, platform, socket, sys, sqlite3
from urllib.parse import parse_qs, urlparse
UTC=datetime.timezone.utc
FORMAT="R2_SHADOW_READINESS_V1"
FILES=("linux.json","linux_trace.json","functional.json","resources.json")
CODE=("runtime.py","raw_store.py","telemetry.py","marks.py","readiness.py","collect_readiness.py")
MAX_AGE=datetime.timedelta(hours=1)

def fail(reason):
    raise RuntimeError("READINESS_"+reason)

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def canonical(obj):
    return hashlib.sha256(json.dumps(obj,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()

def utc(value):
    t=datetime.datetime.fromisoformat(value.replace("Z","+00:00"))
    if t.utcoffset()!=datetime.timedelta(0):fail("TIME_NOT_UTC")
    return t

def environment():
    system=platform.system()
    boot=Path("/proc/sys/kernel/random/boot_id").read_text().strip() if system=="Linux" else ""
    return dict(system=system,host_fingerprint=canonical([socket.gethostname(),system,platform.machine(),platform.release(),boot]),
                python=list(sys.version_info[:2]),numpy=__import__("numpy").__version__,sqlite=__import__("sqlite3").sqlite_version)

def code_hashes():
    here=Path(__file__).resolve().parent
    return {name:sha(here/name) for name in CODE}

def resource_sample(root):
    import resource, shutil
    root=Path(root)
    mem={}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key,value=line.split(":",1)
        mem[key]=int(value.split()[0])*1024
    disk=shutil.disk_usage(root)
    stat=os.statvfs(root)
    files=[p for p in root.rglob("*") if p.is_file()]
    return dict(free_disk_bytes=disk.free,mem_available_bytes=mem["MemAvailable"],
        free_inodes=stat.f_favail,fd_soft_limit=(resource.getrlimit(resource.RLIMIT_NOFILE)[0] if resource.getrlimit(resource.RLIMIT_NOFILE)[0]>=0 else 2**63-1),
        load_per_cpu=os.getloadavg()[0]/(os.cpu_count() or 1),runtime_files=len(files),
        runtime_bytes=sum(p.stat().st_size for p in files))

def check_resources(sample):
    keys={"free_disk_bytes","mem_available_bytes","free_inodes","fd_soft_limit","load_per_cpu","runtime_files","runtime_bytes"}
    if set(sample)!=keys or any(type(v) not in (int,float) or not math.isfinite(v) or v<0 for v in sample.values()):
        fail("RESOURCE_VALUES")
    needed=max(0,10*1024**3-sample["runtime_bytes"])+512*1024**2
    if (sample["free_disk_bytes"]<needed or sample["mem_available_bytes"]<512*1024**2
        or sample["free_inodes"]<4096 or sample["fd_soft_limit"]<1024
        or sample["runtime_files"]>3000 or sample["runtime_bytes"]>10*1024**3):
        fail("RESOURCE_LIMITS")
    return sample

LINUX_PROBE = r'''
import fcntl,json,numpy,os,pathlib,sqlite3,subprocess,sys,tempfile
assert sys.platform.startswith("linux")
with tempfile.TemporaryDirectory(dir=sys.argv[1]) as directory:
 p=pathlib.Path(directory); f=(p/"lock").open("wb");fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
 other=(p/"lock").open("wb")
 exclusive=False
 try:fcntl.flock(other,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:exclusive=True
 other.close()
 d=sqlite3.connect(p/"test.sqlite");d.execute("PRAGMA journal_mode=WAL");d.execute("CREATE TABLE a(c REAL)");d.execute("INSERT INTO a VALUES (300)");d.commit()
 code="import sqlite3,sys,os;d=sqlite3.connect(sys.argv[1]);d.execute('BEGIN IMMEDIATE');d.execute('UPDATE a SET c=1');os._exit(23)"
 r=subprocess.run([sys.executable,"-c",code,str(p/"test.sqlite")],capture_output=True,timeout=10)
 cash=d.execute("SELECT c FROM a").fetchone()[0];integrity=d.execute("PRAGMA integrity_check").fetchone()[0]
 d.close();f.close()
 assert exclusive and r.returncode==23 and cash==300 and integrity=="ok"
 print(json.dumps(dict(system="Linux",lock_exclusive=exclusive,crash_exit=r.returncode,cash_after_crash=cash,integrity=integrity,python=list(sys.version_info[:2]),numpy=numpy.__version__,sqlite=sqlite3.sqlite_version)))
'''

def validate_linux(report,trace,env):
    expected=dict(system="Linux",lock_exclusive=True,crash_exit=23,cash_after_crash=300,
                  integrity="ok",python=[3,12],numpy="2.5.4",sqlite=env["sqlite"])
    if report!={"probe_sha256":hashlib.sha256(LINUX_PROBE.encode()).hexdigest(),"returncode":0,"result":expected}:
        fail("LINUX_RESULT")
    if trace.get("returncode")!=0 or trace.get("stderr") or json.loads(trace["stdout"])!=expected:
        fail("LINUX_TRACE")

def functional_result(refs,raw_root,day,collected_at):
    import runtime
    from raw_store import Archive
    ob=runtime.frozen_modules()
    from public_data import validate_ohlc,guard_kline
    from execution import validate_market,trade_account,valuation
    import numpy as np
    if len(refs)!=9 or len(set(refs.values()))!=9:fail("FUNCTIONAL_RAW_SET")
    if not day<=collected_at<day+datetime.timedelta(days=1):fail("FUNCTIONAL_DAY")
    if not Path(raw_root).is_dir():fail("FUNCTIONAL_RAW_MISSING")
    archive=Archive(raw_root)
    parsed={}
    receipts={}
    for kind,path in [("kline","/v5/market/kline"),("meta","/v5/market/instruments-info"),("book","/v5/market/orderbook")]:
        for symbol in ob.SYMS:
            key=kind+":"+symbol
            body,r=archive.read(refs[key])
            u=urlparse(r["url"]);query=parse_qs(u.query)
            if (u.scheme!="https" or u.netloc!="api.bybit.com" or u.path!=path
                or query.get("category")!=["spot"] or query.get("symbol")!=[symbol]
                or r["http_status"]!=200 or r.get("complete") is not True):
                fail("FUNCTIONAL_SOURCE")
            available=utc(r["available_at"])
            if not day<=available<=collected_at:fail("FUNCTIONAL_RECEIPT_TIME")
            obj=json.loads(body)
            if obj.get("retCode")!=0:fail("FUNCTIONAL_API_ERROR")
            if kind=="kline":
                start=int((day-datetime.timedelta(days=29)).timestamp()*1000);end=int(day.timestamp()*1000)
                if query.get("start")!=[str(start)] or query.get("end")!=[str(end-1)] or query.get("interval")!=["D"]:
                    fail("FUNCTIONAL_CANDLE_WINDOW")
                guard_kline(start,end-1)
                _,bars=validate_ohlc(obj["result"]["list"],86400000,start,end)
                parsed[key]=[v[3] for v in bars]
            else:parsed[key]=obj["result"]
            receipts[key]=r
    markets=[]
    for symbol in ob.SYMS:
        meta=parsed["meta:"+symbol]
        if meta["category"]!="spot" or len(meta["list"])!=1:fail("FUNCTIONAL_META")
        m=validate_market(meta["list"][0],parsed["book:"+symbol],int(utc(receipts["book:"+symbol]["available_at"]).timestamp()*1000))
        if not 0<=collected_at.timestamp()*1000-m["book"]["ts"]<=5000:fail("FUNCTIONAL_BOOK_AGE")
        markets.append(m)
    if max(m["book"]["ts"] for m in markets)-min(m["book"]["ts"] for m in markets)>5000:fail("FUNCTIONAL_BOOK_SKEW")
    signal=ob.signal(np.array([parsed["kline:"+s] for s in ob.SYMS]).T)
    accounts={}
    for strategy in ["BASELINE","R2"]:
        for cap in [300,1000,10000]:
            for cost in ["BASE","STRESS"]:
                for policy in ["MODEL_C5","OBSERVED_BOOK"]:
                    c,q,events=trade_account(float(cap),[0.,0.,0.],signal[strategy],markets,cost,policy)
                    accounts[f"{strategy}:{cap}:{cost}:{policy}"]=dict(cash=c,qty=q,events=events,
                        valuation=valuation(c,q,markets,cost,policy))
    return dict(accounts=accounts,weights={s:signal[s].tolist() for s in ["BASELINE","R2"]},
                real_orders=0,demo_orders=0,private_api_calls=0,forward_account_mutations=0)

def validate(root,policy,cohort,now=None):
    # No process lock, DB creation, HTTP request, or approval substitution occurs here.
    import runtime
    root=Path(root).resolve()
    now=now or datetime.datetime.now(UTC)
    folder=root/"readiness"
    receipt_path=root/"START_RECEIPT.json"
    is_recovery=receipt_path.is_file()
    try:
        manifest_path=folder/"manifest.json"
        if not manifest_path.is_file():fail("MISSING_MANIFEST")
        manifest_sha=sha(manifest_path)
        if policy.get("readiness_manifest_sha256")!=manifest_sha:fail("MANIFEST_HASH")
        m=json.loads(manifest_path.read_text())
        env=environment()
        if env["system"]!="Linux" or env["python"]!=[3,12] or env["numpy"]!="2.5.4":fail("LINUX_HOST_REQUIRED")
        if m["format"]!=FORMAT or m["environment"]!=env or m["runtime_path"]!=str(root):
            fail("HOST_OR_RUNTIME_BINDING")
        when=utc(m["collected_at"])
        if not is_recovery:
            if not datetime.timedelta(0)<=now-when<=MAX_AGE:fail("EXPIRED_OR_FUTURE")
        else:
            if when>now:fail("EXPIRED_OR_FUTURE")
        if m["cohort_sha256"]!=canonical(cohort):fail("COHORT_BINDING")
        runtime.verify_frozen()
        if m["frozen_manifest_sha256"]!=sha(runtime.HERE/"FROZEN_MANIFEST.json"):
            fail("SOURCE_BINDING")
        current_code=code_hashes()
        if not is_recovery:
            if m["code_sha256"]!=current_code:fail("SOURCE_BINDING")
        else:
            # In recovery mode:
            # 1. Core modules that must remain 100% identical to initial manifest:
            for name in ("runtime.py","raw_store.py","telemetry.py","marks.py","collect_readiness.py"):
                if m["code_sha256"].get(name)!=current_code.get(name):fail("SOURCE_BINDING")
            # 2. readiness.py must match either initial manifest OR the approved R2-RECOVERY-001 patch:
            patch_file=runtime.HERE/"RECOVERY_PATCH.json"
            if not patch_file.is_file():fail("MISSING_RECOVERY_PATCH")
            patch=json.loads(patch_file.read_text(encoding="utf-8"))
            if patch.get("protocol")!="R2-RECOVERY-001":fail("INVALID_RECOVERY_PATCH_PROTOCOL")
            approved_readiness_sha=patch.get("approved_patch",{}).get("readiness.py")
            if current_code.get("readiness.py") not in (m["code_sha256"].get("readiness.py"),approved_readiness_sha):
                fail("UNAUTHORIZED_READINESS_PATCH")
        if is_recovery:
            receipt=json.loads(receipt_path.read_text())
            if receipt.get("status") not in ("ARMED","STARTED_UNVERIFIED","OBSERVING"):fail("START_RECEIPT_STATUS")
            if receipt.get("cohort")!=cohort:fail("COHORT_CHANGED")
            prev_ready=receipt.get("readiness") or {}
            if prev_ready.get("manifest_sha256")!=manifest_sha:fail("START_RECEIPT_MANIFEST_MISMATCH")
            if prev_ready.get("host_fingerprint")!=env["host_fingerprint"]:fail("HOST_OR_RUNTIME_BINDING")
            db_file=root/"observer.sqlite"
            if not db_file.is_file():fail("MISSING_LEDGER")
            try:
                db=sqlite3.connect(db_file.resolve().as_uri()+"?mode=ro",uri=True)
                try:
                    if db.execute("PRAGMA integrity_check").fetchone()[0]!="ok":fail("LEDGER_CORRUPT")
                    cnt=db.execute("SELECT count(*) FROM accounts").fetchone()[0]
                    if cnt!=24:fail("PROFILE_MATRIX_CORRUPT")
                finally:
                    db.close()
            except sqlite3.Error:
                fail("LEDGER_CORRUPT")
        if set(m["files"])!=set(FILES):fail("ARTIFACT_SET")
        for name in FILES:
            if m["files"][name]!=sha(folder/name):fail("ARTIFACT_HASH")
        reports={n:json.loads((folder/n).read_text()) for n in FILES}
        validate_linux(reports["linux.json"],reports["linux_trace.json"],env)
        check_resources(reports["resources.json"])
        check_resources(resource_sample(root))
        functional=reports["functional.json"]
        result=functional_result(functional["raw_references"],folder/"raw",utc(functional["day"]),when)
        if result!=functional["result"]:fail("FUNCTIONAL_REPLAY_MISMATCH")
        approval=policy.get("user_authorization") or {}
        if (policy.get("deployment_authorized") is not True or approval.get("scope")!="R2_SHADOW_START"
            or not isinstance(approval.get("reference"),str) or not approval["reference"].strip()
            or approval.get("t0_utc")!=cohort["t0_utc"]):
            fail("EXPLICIT_USER_AUTHORIZATION_REQUIRED")
        if policy.get("authorized_start_utc") is not None and policy.get("authorized_start_utc")!=cohort["t0_utc"]:fail("AUTHORIZED_T0_MISMATCH")
        code_sha_to_return=receipt.get("readiness",{}).get("code_sha256",m["code_sha256"]) if is_recovery else m["code_sha256"]
        return dict(status="PASS",manifest_sha256=manifest_sha,collected_at=m["collected_at"],
                    host_fingerprint=env["host_fingerprint"],functional_profiles=len(result["accounts"]), code_sha256=code_sha_to_return)
    except RuntimeError:
        raise
    except (OSError,ValueError,KeyError,TypeError,OverflowError,AssertionError,sqlite3.Error) as e:
        fail("INVALID_EVIDENCE:"+type(e).__name__)
