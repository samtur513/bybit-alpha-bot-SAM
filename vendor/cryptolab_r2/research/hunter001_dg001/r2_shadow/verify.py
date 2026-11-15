"""Offline acceptance evidence. Does not install, contact an exchange, or run a daemon."""
from pathlib import Path
import csv, hashlib, io, json, os, platform, sqlite3, subprocess, sys, tempfile, time, tracemalloc, zipfile
import runtime
from raw_store import Archive
from telemetry import export
HERE=Path(__file__).resolve().parent
REPO=runtime.REPO
EVIDENCE=HERE/"evidence"

def write(name,obj):
    (EVIDENCE/name).write_text(json.dumps(obj,indent=2,allow_nan=False)+"\n",encoding="utf-8")

def execute():
    EVIDENCE.mkdir(exist_ok=True)
    runtime.frozen_modules()
    manifest=runtime.verify_frozen()
    # Prevent pytest and optional installed plugins from accessing any external network.
    guard=EVIDENCE/"network_guard"
    guard.mkdir(exist_ok=True)
    (guard/"sitecustomize.py").write_text("import socket\ndef deny(*a,**k): raise RuntimeError('OFFLINE_NETWORK_FORBIDDEN')\nsocket.socket.connect=deny\nsocket.create_connection=deny\n",encoding="utf-8")
    env=dict(os.environ,PYTHONPATH=os.pathsep.join([str(guard),str(HERE),str(HERE.parent/"r2_validation"),str(HERE.parent/"t01_profit_rank")]))
    commands=[]
    for label,source in [("original",HERE.parent/"r2_validation/test_validation.py"),("shadow",HERE/"test_shadow.py"),("qa_fix",HERE/"test_qa_fix.py")]:
        with tempfile.TemporaryDirectory(prefix="test-"+label+"-",dir=EVIDENCE) as tmp:
            cmd=[sys.executable,"-m","pytest","-q",str(source),"--basetemp="+str(Path(tmp)/"pytest"),"--junitxml="+str(EVIDENCE/(label+"_tests.xml"))]
            result=subprocess.run(cmd,cwd=REPO,env=env,text=True,capture_output=True,timeout=120)
            (EVIDENCE/(label+"_tests.txt")).write_text(result.stdout+result.stderr,encoding="utf-8")
            commands.append(dict(suite=label,returncode=result.returncode,network="DENIED_AT_SOCKET_LAYER"))
            if result.returncode:
                write("VERIFICATION.json",dict(status="BLOCKED",tests=commands))
                raise SystemExit(result.stdout[-3000:])
    # Deterministic representative payload; synthetic size/latency is not a live VPS budget.
    with tempfile.TemporaryDirectory(prefix="resource-",dir=EVIDENCE) as tmp:
        archive=Archive(tmp,reserve=0)
        rng=__import__("numpy").random.default_rng(61115)
        body=json.dumps(dict(retCode=0,result=dict(b=[[str(100+i),str(float(rng.uniform(1,100)))] for i in range(50)]))).encode()
        raw_sum=0
        tracemalloc.start()
        started=time.perf_counter()
        for i in range(4410):
            archive.capture(io.BytesIO(body),dict(id=f"{i:032x}",available_at="2026-11-15T00:00:00+00:00",url="https://api.bybit.com/v5/market/orderbook",http_status=200))
            raw_sum+=len(body)
        elapsed=time.perf_counter()-started
        _,peak=tracemalloc.get_traced_memory()
        tracemalloc.stop()
        files=[p for p in Path(tmp).iterdir() if p.is_file()]
        stored=sum(p.stat().st_size for p in files)
        archive.recover()
        write("RESOURCE_PROFILE.json",dict(kind="SYNTHETIC_OFFLINE_NOT_VPS_MEASUREMENT",responses=4410,
            original_body_bytes=raw_sum,stored_bytes=stored,files_after_commit=len(files),
            elapsed_seconds=elapsed,python_peak_allocated_bytes=peak,includes_numpy_rss=False,
            estimated_184_days_bytes_same_fixture=stored*184,quota_raw_bytes=8*1024**3,
            quota_daily_bytes=256*1024**2,quota_total_runtime_bytes=10*1024**3,
            min_free_disk_bytes=512*1024**2,retention_deletion=False))
    from datetime import datetime,timezone
    with tempfile.TemporaryDirectory(prefix="runtime-",dir=EVIDENCE) as tmp:
        folder=Path(tmp)/"independent"
        future=datetime(2030,1,1,tzinfo=timezone.utc)
        cohort=dict(status="PLANNED",t0_utc=future.isoformat(),minimum_end_utc=runtime.add_months(future).isoformat(),
            initial_capital_usdt=300,participants=["TORG_V32_NATIVE_NEW_COHORT","TORG_V32_REALISTIC_SHADOW","CRYPTOLAB_R2_SHADOW"])
        runtime.prepare(folder,cohort,now=datetime(2026,11,15,tzinfo=timezone.utc))
        write("RECOVERY.json",runtime.recover(folder))
        data=export(folder,folder/"telemetry.json",now=datetime(2026,11,15,tzinfo=timezone.utc))
        # Shareable example carries synthetic/null values and a future example cohort only.
        write("telemetry.example.json",data)
        db=sqlite3.connect(folder/"observer.sqlite")
        rows=db.execute("SELECT id,payload FROM accounts ORDER BY id").fetchall()
        db.close()
        with (HERE/"R2_24_ACCOUNTS_MAPPING.csv").open("w",newline="",encoding="utf-8") as out:
            writer=csv.writer(out)
            writer.writerow(["profile","strategy","capital_usdt","cost","policy","dashboard_role"])
            for key,payload in rows:
                a=json.loads(payload)
                role="PRIMARY" if key=="R2:300:BASE:OBSERVED_BOOK" else "STRESS" if key=="R2:300:STRESS:OBSERVED_BOOK" else "T01_CONTROL" if key=="BASELINE:300:BASE:OBSERVED_BOOK" else "INTERNAL_CONTROL"
                writer.writerow([key,a["strategy"],a["capital"],a["cost"],a["policy"],role])
    # Archive a nonzero hard-crash reproduction independently of the empty prepare check.
    with tempfile.TemporaryDirectory(prefix="nonzero-",dir=EVIDENCE) as tmp:
        result=subprocess.run([sys.executable,str(HERE/"recovery_scenario.py"),"--output",str(Path(tmp)/"scenario")],
            cwd=REPO,env=env,text=True,capture_output=True,timeout=90)
        if result.returncode:raise RuntimeError("NONZERO_RECOVERY_FAILED:"+result.stderr)
        scenario=Path(tmp)/"scenario"
        for name in ["RECOVERY_NONZERO.json"]:
            (EVIDENCE/name).write_bytes((scenario/name).read_bytes())
        with zipfile.ZipFile(EVIDENCE/"RECOVERY_NONZERO_INPUTS_AND_LEDGERS.zip","w",zipfile.ZIP_DEFLATED) as z:
            for file in sorted(scenario.rglob("*")):
                if file.is_file():z.write(file,file.relative_to(scenario).as_posix())
    # Only audited sources/configs; no historical datasets, secrets, Demo code or state.
    members=list(manifest["files"])+[(HERE/p).relative_to(REPO).as_posix() for p in
        ["runtime.py","raw_store.py","telemetry.py","marks.py","readiness.py","collect_readiness.py","FROZEN_MANIFEST.json","AUTHORIZATION.json","COHORT_PLAN.json"]]
    bundle=HERE/"R2_SHADOW_RUNTIME.zip"
    with zipfile.ZipFile(bundle,"w",compression=zipfile.ZIP_DEFLATED) as z:
        for name in sorted(members):
            info=zipfile.ZipInfo(name,date_time=(2026,11,15,0,0,0))
            info.compress_type=zipfile.ZIP_DEFLATED
            z.writestr(info,(REPO/name).read_bytes())
    with zipfile.ZipFile(bundle) as z:
        if z.testzip() is not None or sorted(z.namelist())!=sorted(members):
            raise RuntimeError("BUNDLE_INVALID")
    write("BUNDLE_MANIFEST.json",dict(source_commit=runtime.BASE,archive=bundle.name,
        sha256=hashlib.sha256(bundle.read_bytes()).hexdigest(),
        members={p:hashlib.sha256((REPO/p).read_bytes()).hexdigest() for p in sorted(members)}))
    import xml.etree.ElementTree as ET
    counts={}
    for label in ["original","shadow","qa_fix"]:
        suite=next(ET.parse(EVIDENCE/(label+"_tests.xml")).getroot().iter("testsuite"))
        counts[label]={k:int(suite.attrib[k]) for k in ["tests","failures","errors","skipped"]}
    write("VERIFICATION.json",dict(status="OFFLINE_PREPARED",deployment="VPS_PENDING",source_commit=runtime.BASE,
        platform=platform.platform(),python=sys.version,numpy=__import__("numpy").__version__,
        tests=counts,commands=commands,source_files_verified=len(manifest["files"]),
        exchange_requests=0,real_orders=0,demo_orders=0,private_api_calls=0,
        vps_contacts=0,installs=0,service_changes=0,
        pending=["VPS_RESOURCE_AND_LINUX_GATE","THREE_PARTICIPANTS_READY","RECEIVER_CONTRACT_ACCEPTANCE","FUTURE_T0_AUTHORIZATION"],
        economic_status="INCONCLUSIVE_REAL_CAPITAL_NO_GO"))
    from check_contract import check
    check()
    write("QA_FIX_001.json",dict(status="OFFLINE_PREPARED",deployment="VPS_PENDING",
        issue_parent_commit="a2f1820d9b6da02c3f24cc8a5c1680016ea2b54e",
        telemetry="CURRENT_QUOTE_BATCH_AND_READ_ONLY_BALANCE_SNAPSHOT; NO_EXTRA_HTTP",
        readiness="HASHED_HOST_COHORT_CODE_BOUND_ARTIFACTS; ALL ACTUAL VPS RESULTS STILL MISSING",
        readiness_positive_tests="SYNTHETIC_OFFLINE_FIXTURES_NOT_VPS_EVIDENCE",
        recovery="132_ORDER_EVENTS_ALL24_NONZERO; HARD_EXIT_BEFORE_AND_AFTER_COMMIT",
        wire_format_changed=False,torg_bot_changes=0,observer_source_changes=0,real_orders=0,demo_orders=0,
        actual_vps_checks_run=False,launch_authorized=False))
    seal()

def seal():
    files=[p for p in HERE.rglob("*") if p.is_file() and "__pycache__" not in p.parts
           and ".pytest_cache" not in p.parts and "network_guard" not in p.parts and p.name!="SHA256SUMS.txt"]
    lines=[hashlib.sha256(p.read_bytes()).hexdigest()+"  "+p.relative_to(HERE).as_posix() for p in sorted(files)]
    (HERE/"SHA256SUMS.txt").write_text("\n".join(lines)+"\n",encoding="utf-8")

if __name__=="__main__":
    if "--seal-only" in sys.argv:
        seal()
    else:
        execute()
