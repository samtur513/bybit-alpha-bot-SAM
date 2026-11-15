"""Reproducible nonzero recovery experiment; synthetic, offline, never a daemon."""
from pathlib import Path
import argparse, datetime, gzip, hashlib, json, os, sqlite3, subprocess, sys, zipfile
import numpy as np
import runtime
from raw_store import Archive
UTC=datetime.timezone.utc
DAY=datetime.datetime(2030,1,1,tzinfo=UTC)

def jsonhash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()

def cohort():
    return dict(status="PLANNED",t0_utc=DAY.isoformat(),minimum_end_utc=runtime.add_months(DAY).isoformat(),
        initial_capital_usdt=300,participants=["TORG_V32_NATIVE_NEW_COHORT","TORG_V32_REALISTIC_SHADOW","CRYPTOLAB_R2_SHADOW"])

def inputs(root,day):
    from execution import validate_market
    offset=(day-DAY).days
    closes=100*np.exp(np.arange(29)[:,None]*np.array([[.01,.02,.03] if offset==0 else [.03,-.01,.02]]))
    signal=runtime.frozen_modules().signal(closes)
    markets=[]
    archive=Archive(Path(root)/"raw",reserve=0)
    for j,s in enumerate(["BTCUSDT","ETHUSDT","SOLUSDT"]):
        mid=[100.,200.,300.][j]*(1+offset*.05)
        meta=dict(symbol=s,status="Trading",lotSizeFilter=dict(basePrecision=str([.000001,.00001,.0001][j]),
            quotePrecision="0.000001",minOrderAmt="5",maxMarketOrderQty="100000"))
        stamp=int((day+datetime.timedelta(hours=1)).timestamp()*1000)
        book=dict(s=s,b=[[str(mid-.1),"100000"]],a=[[str(mid+.1),"100000"]],ts=stamp)
        body=json.dumps(dict(SYNTHETIC_OFFLINE_FIXTURE=True,day=day.isoformat(),symbol=s,
            closes=closes[:,j].tolist(),meta=meta,book=book),sort_keys=True).encode()
        h=hashlib.sha256(body).hexdigest()
        source=Path(root)/"raw"/"synthetic-input.gz"
        source.write_bytes(gzip.compress(body,mtime=0))
        receipt=archive.pack(source,dict(id=h[:32],sha256=h,bytes=len(body),complete=True,
            url="fixture://recovery-nonzero/"+s,available_at=(day+datetime.timedelta(hours=1)).isoformat(),
            ingested_at=(day+datetime.timedelta(hours=1)).isoformat(),rtt_ms=0,http_status=200,
            provenance="SYNTHETIC_OFFLINE_NOT_EXCHANGE_DATA"))
        source.unlink()
        m=validate_market(meta,book,stamp)
        m["metadata_receipt"]=m["book_receipt"]=receipt
        markets.append(m)
    return signal,markets,[m["book_receipt"] for m in markets]

def instance_at(root,day):
    ob=runtime.frozen_modules()
    instance=runtime.make_observer(root)
    sig,markets,receipts=inputs(root,day)
    instance.signals=lambda *_:(sig,receipts)
    instance.markets=lambda **_:markets
    return instance

def state(root):
    db=sqlite3.connect((Path(root)/"observer.sqlite").resolve().as_uri()+"?mode=ro",uri=True)
    try:
        tables={name:db.execute("SELECT * FROM "+name+" ORDER BY 1").fetchall() for name in ["accounts","decisions","events"]}
        accounts={k:json.loads(v) for k,v in tables["accounts"]}
        positive={k:0 for k in accounts}
        fees={k:0. for k in accounts}
        for _,kind,_,payload in tables["events"]:
            if kind=="ORDER":
                z=json.loads(payload)
                key=f"{z['strategy']}:{z['capital']}:{z['cost']}:{z['policy']}"
                positive[key]+=z["gross_qty"]>0
                fees[key]+=z["fee_quote"]
        return dict(tables_sha256=jsonhash(tables),accounts=accounts,positive_legs=positive,fees_usdt=fees,
                    order_events=sum(r[1]=="ORDER" for r in tables["events"]),
                    execution_ids=[r[0] for r in tables["events"] if r[1]=="EXECUTION"])
    finally:
        db.close()

def child(root,phase):
    ob=runtime.frozen_modules()
    day=DAY+datetime.timedelta(days=1)
    ob.utc=lambda:day+datetime.timedelta(hours=1)
    instance=instance_at(root,day)
    if phase=="before_commit":
        original=instance.store.event
        def event(eid,kind,payload):
            original(eid,kind,payload)
            if kind=="EXECUTION":os._exit(23)
        instance.store.event=event
    else:
        instance.store.export=lambda:os._exit(24)
    instance.execute(day)
    raise RuntimeError("CRASH_POINT_NOT_REACHED")

def experiment(out):
    out=Path(out).resolve()
    out.mkdir(parents=True,exist_ok=False)
    ob=runtime.frozen_modules()
    prior=ob.utc
    roots={name:out/name for name in ["reference","before_commit","after_commit"]}
    snapshots={}
    try:
        for name,root in roots.items():
            runtime.prepare(root,cohort(),now=DAY-datetime.timedelta(days=1))
            instance=instance_at(root,DAY)
            ob.utc=lambda:DAY+datetime.timedelta(seconds=5)
            instance.decide(DAY)
            ob.utc=lambda:DAY+datetime.timedelta(hours=1)
            instance.execute(DAY)
            instance.store.db.close()
            first=state(root)
            if len(first["accounts"])!=24 or not all(v>0 for v in first["positive_legs"].values()):
                raise RuntimeError("ALL24_NONZERO_PRECONDITION_FAILED")
            day=DAY+datetime.timedelta(days=1)
            instance=instance_at(root,day)
            ob.utc=lambda:day+datetime.timedelta(seconds=5)
            instance.decide(day)
            instance.store.db.close()
            snapshots[name]=dict(before_crash=state(root))
        reference=instance_at(roots["reference"],day)
        ob.utc=lambda:day+datetime.timedelta(hours=1)
        reference.execute(day)
        reference.store.db.close()
        expected=state(roots["reference"])
        reports=[]
        for phase in ["before_commit","after_commit"]:
            root=roots[phase]
            r=subprocess.run([sys.executable,str(Path(__file__).resolve()),"--child",phase,"--data-dir",str(root)],
                             capture_output=True,text=True,timeout=30)
            expected_exit=23 if phase=="before_commit" else 24
            if r.returncode!=expected_exit:raise RuntimeError("CHILD_CRASH_FAILURE:"+r.stderr)
            after=state(root)
            if phase=="before_commit" and after!=snapshots[phase]["before_crash"]:
                raise RuntimeError("ROLLBACK_MISMATCH")
            if phase=="after_commit" and after!=expected:raise RuntimeError("COMMIT_MISMATCH")
            recovered=runtime.recover(root)
            instance=instance_at(root,day)
            ob.utc=lambda:day+datetime.timedelta(hours=1)
            instance.execute(day)
            final=state(root)
            instance.execute(day)
            repeat=state(root)
            instance.store.db.close()
            if final!=expected or repeat!=final:raise RuntimeError("RECOVERY_RETRY_MISMATCH")
            snapshots[phase].update(after_crash=after,after_recovery=final)
            reports.append(dict(crash=phase,exit_code=r.returncode,profiles_compared=24,
                nonzero_profiles_before_crash=24,checkpoint_matches_reference=True,retry_idempotent=True,
                recovered=recovered,final_tables_sha256=final["tables_sha256"],
                per_profile={k:dict(positive_legs=final["positive_legs"][k],fees_usdt=final["fees_usdt"][k],
                    account_sha256=jsonhash(final["accounts"][k]),matches_reference=True)
                    for k in sorted(final["accounts"])}))
        snapshots["reference"]["final"]=expected
        (out/"ALL_24_ACCOUNTS.json").write_text(json.dumps(snapshots,indent=2)+"\n",encoding="utf-8")
        report=dict(status="PASS",kind="SYNTHETIC_OFFLINE_NONZERO_RECOVERY",network_requests=0,
            real_orders=0,demo_orders=0,reference_order_events=expected["order_events"],
            reference_nonzero_profiles=24,scenarios=reports)
        (out/"RECOVERY_NONZERO.json").write_text(json.dumps(report,indent=2)+"\n",encoding="utf-8")
        return report
    finally:
        ob.utc=prior

if __name__=="__main__":
    # Defense in depth: even a broken fixture cannot contact a network.
    import socket
    def deny(*a,**k):raise RuntimeError("OFFLINE_SCENARIO_NETWORK_FORBIDDEN")
    socket.socket.connect=deny
    socket.create_connection=deny
    p=argparse.ArgumentParser()
    p.add_argument("--output")
    p.add_argument("--child",choices=["before_commit","after_commit"])
    p.add_argument("--data-dir")
    a=p.parse_args()
    if a.child:child(a.data_dir,a.child)
    else:print(json.dumps(experiment(a.output)))
