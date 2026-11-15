"""Future, separately authorized Linux readiness collection. Never starts paper trading."""
from pathlib import Path
import argparse, datetime, json, subprocess, sys
import readiness
import runtime

def collect(root,policy):
    # Both switches require a real separate permission in the future, never inferred here.
    if policy.get("readiness_collection_authorized") is not True or not policy.get("collection_approval_reference"):
        raise RuntimeError("READINESS_COLLECTION_NOT_AUTHORIZED")
    env=readiness.environment()
    if env["system"]!="Linux":
        raise RuntimeError("READINESS_LINUX_HOST_REQUIRED")
    runtime.frozen_modules()
    root=Path(root).resolve()
    cohort=json.loads((root/"COHORT.json").read_text())
    readiness.check_resources(readiness.resource_sample(root))
    folder=root/"readiness"
    folder.mkdir(exist_ok=False)
    result=subprocess.run([sys.executable,"-c",readiness.LINUX_PROBE,str(folder)],
                          text=True,capture_output=True,timeout=30)
    trace=dict(returncode=result.returncode,stdout=result.stdout,stderr=result.stderr)
    if result.returncode:raise RuntimeError("READINESS_LINUX_PROBE_FAILED")
    linux=dict(probe_sha256=readiness.hashlib.sha256(readiness.LINUX_PROBE.encode()).hexdigest(),
               returncode=0,result=json.loads(result.stdout))
    readiness.validate_linux(linux,trace,env)
    api=runtime.CompactPublic(folder/"raw")
    day=datetime.datetime.now(runtime.UTC).replace(hour=0,minute=0,second=0,microsecond=0)
    start=int((day-datetime.timedelta(days=29)).timestamp()*1000)
    end=int(day.timestamp()*1000)
    refs={}
    for symbol in ["BTCUSDT","ETHUSDT","SOLUSDT"]:
        _,r=api.get("/v5/market/kline",category="spot",symbol=symbol,interval="D",start=start,end=end-1,limit=29)
        refs["kline:"+symbol]=r["file"]
        _,r=api.get("/v5/market/instruments-info",category="spot",symbol=symbol)
        refs["meta:"+symbol]=r["file"]
    from concurrent.futures import ThreadPoolExecutor
    def book(symbol):
        _,r=api.get("/v5/market/orderbook",category="spot",symbol=symbol,limit=50)
        return "book:"+symbol,r["file"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        refs.update(pool.map(book,["BTCUSDT","ETHUSDT","SOLUSDT"]))
    collected=datetime.datetime.now(runtime.UTC)
    functional=dict(day=day.isoformat(),raw_references=refs,
        result=readiness.functional_result(refs,folder/"raw",day,collected))
    reports={"linux.json":linux,"linux_trace.json":trace,"functional.json":functional,
             "resources.json":readiness.check_resources(readiness.resource_sample(root))}
    for name,value in reports.items():
        runtime.frozen_modules().atomic(folder/name,value)
    manifest=dict(format=readiness.FORMAT,environment=env,runtime_path=str(root),collected_at=collected.isoformat(),
        cohort_sha256=readiness.canonical(cohort),frozen_manifest_sha256=readiness.sha(runtime.HERE/"FROZEN_MANIFEST.json"),
        code_sha256=readiness.code_hashes(),files={name:readiness.sha(folder/name) for name in readiness.FILES})
    runtime.frozen_modules().atomic(folder/"manifest.json",manifest)
    return dict(status="COLLECTED_NOT_AUTHORIZED_TO_START",manifest_sha256=readiness.sha(folder/"manifest.json"))

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--data-dir",required=True)
    p.add_argument("--policy",required=True)
    a=p.parse_args()
    print(json.dumps(collect(a.data_dir,json.loads(Path(a.policy).read_text()))))
