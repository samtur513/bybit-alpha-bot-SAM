import copy, datetime, gzip, hashlib, io, itertools, json, os, socket, sqlite3, subprocess, sys
from pathlib import Path
import numpy as np
import pytest
import runtime
from raw_store import Archive
from telemetry import export, PROFILES
ob = runtime.frozen_modules()
UTC = datetime.timezone.utc
DAY = datetime.datetime(2027,1,1,tzinfo=UTC)

@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*a, **k):
        pytest.fail("UNEXPECTED_NETWORK")
    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)

def cohort(day=DAY):
    return dict(status="PLANNED", t0_utc=day.isoformat(), minimum_end_utc=runtime.add_months(day).isoformat(),
        initial_capital_usdt=300, participants=["TORG_V32_NATIVE_NEW_COHORT","TORG_V32_REALISTIC_SHADOW","CRYPTOLAB_R2_SHADOW"])

def prepare(path):
    return runtime.prepare(path,cohort(),now=DAY-datetime.timedelta(days=1))

def raw(archive, body=b'{"retCode":0,"time":1000,"result":{}}'):
    rid=hashlib.md5(body+os.urandom(4)).hexdigest()
    return archive.capture(io.BytesIO(body),dict(id=rid,url="https://api.bybit.com/v5/market/time",available_at=DAY.isoformat(),http_status=200))

def markets(archive, level=100.):
    receipt=raw(archive)
    result=[]
    from execution import validate_market
    for j,s in enumerate(["BTCUSDT","ETHUSDT","SOLUSDT"]):
        meta=dict(symbol=s,status="Trading",lotSizeFilter=dict(basePrecision=str([.000001,.00001,.0001][j]),quotePrecision="0.000001",minOrderAmt="5",maxMarketOrderQty="100000"))
        book=dict(s=s,b=[[str(level-1),"10000"]],a=[[str(level+1),"10000"]],ts=100000)
        m=validate_market(meta,book,100500)
        m["metadata_receipt"]=m["book_receipt"]=receipt
        result.append(m)
    return result,receipt

def snapshot(store):
    return dict(accounts=store.accounts(),events=store.db.execute("SELECT id,kind,payload FROM events ORDER BY id").fetchall(),
        decisions=store.db.execute("SELECT * FROM decisions ORDER BY day").fetchall())

def test_frozen_manifest_and_profile_matrix(tmp_path):
    assert len(runtime.verify_frozen()["files"])==9
    root=prepare(tmp_path/"new")
    assert runtime.recover(root)["profiles"]==24
    db=sqlite3.connect(root/"observer.sqlite")
    assert db.execute("SELECT count(*) FROM accounts").fetchone()[0]==24
    db.close()
    with pytest.raises(FileExistsError):prepare(root)

@pytest.mark.parametrize("mask",list(itertools.product([False,True],repeat=3)))
def test_signal_parity_all_eligibility(mask):
    rng=np.random.default_rng(42)
    returns=rng.normal(0,.01,(28,3))
    returns-=returns.mean(axis=0)
    returns+=np.array([.003 if x else -.003 for x in mask])
    cl=np.vstack([np.ones(3)*100,100*np.exp(np.cumsum(returns,axis=0))])
    got=ob.signal(cl)
    e=cl[-1]>cl[0]
    mom=np.log(cl[-1]/cl[0])
    vol=np.log(cl[1:]/cl[:-1]).std(axis=0,ddof=1)*np.sqrt(28)
    score=mom/vol
    weights=e.astype(float)/3
    if e.all():
        ids=sorted(range(3),key=lambda j:(-score[j],ob.SYMS[j]))
        weights[:]=0
        weights[ids[:2]]=.5
    assert np.array_equal(got["eligible"],e)
    assert np.array_equal(got["BASELINE"],e.astype(float)/3)
    assert np.array_equal(got["R2"],weights)
    assert np.array_equal(got["score"],score)

def test_signal_random_ties_flat_and_ulp():
    rng=np.random.default_rng(61115)
    for _ in range(100):
        cl=100*np.exp(np.cumsum(rng.normal(.001,.02,(29,3)),axis=0))
        actual=ob.signal(cl)
        # Independent 30-day parent factor calculation for target day 29.
        x=np.repeat(np.vstack([cl,cl[-1:]])[:,None,:],24,axis=1).reshape(720,3)
        bars=np.stack([x,x,x,x],axis=1)
        t=np.arange(720)*60
        _,_,e,s=sys.modules["core"].factors(t,bars)
        expected=sys.modules["core"].targets(e,s,"R2")[29]
        assert np.array_equal(actual["R2"],expected)
    flat=ob.signal(np.ones((29,3))*100)
    assert not flat["eligible"].any() and flat["R2"].sum()==0
    c=np.ones((29,3))*100
    c[-1,0]=np.nextafter(100.,np.inf)
    assert ob.signal(c)["R2"][0]==1/3
    cl=np.repeat((100*np.exp(np.cumsum(rng.normal(.01,.01,29))))[:,None],3,axis=1)
    assert np.array_equal(ob.signal(cl)["R2"],[.5,.5,0])

@pytest.mark.parametrize("size",[0,1,65536,1000001])
def test_raw_lossless_and_restart(tmp_path,size):
    a=Archive(tmp_path,reserve=0)
    body=np.random.default_rng(7).bytes(size)
    receipt=raw(a,body)
    assert a.read(receipt["file"])[0]==body
    assert len(list(tmp_path.glob("raw-*.sqlite")))==1
    assert not list(tmp_path.glob("capture-*"))
    Archive(tmp_path,reserve=0).recover()
    assert receipt["sha256"]==hashlib.sha256(body).hexdigest()
    assert receipt["available_at"]>=receipt["started_at"] if "started_at" in receipt else True

def test_pack_idempotence_and_conflict(tmp_path):
    a=Archive(tmp_path,reserve=0)
    body=b"exact bytes"
    r=raw(a,body)
    source=tmp_path/"input.gz"
    source.write_bytes(gzip.compress(body,mtime=0))
    assert a.pack(source,{k:v for k,v in r.items() if k!="file"})==r
    with pytest.raises(ValueError):a.pack(source,dict(r,bytes=99))

def test_raw_interrupted_after_commit_recovery(tmp_path,monkeypatch):
    a=Archive(tmp_path,reserve=0)
    original=Path.unlink
    def fail(path,*args,**kwargs):
        if path.name.startswith("capture-") and path.suffix==".json":raise RuntimeError("CRASH_AFTER_RAW_COMMIT")
        return original(path,*args,**kwargs)
    monkeypatch.setattr(Path,"unlink",fail)
    with pytest.raises(RuntimeError):raw(a,b"body")
    monkeypatch.setattr(Path,"unlink",original)
    a.recover()
    assert not list(tmp_path.glob("capture-*"))
    db=sqlite3.connect(next(tmp_path.glob("raw-*.sqlite")))
    assert db.execute("SELECT count(*) FROM raw").fetchone()[0]==1
    with pytest.raises(sqlite3.IntegrityError):db.execute("DELETE FROM raw")
    db.close()

def test_partial_capture_and_quota_fail_closed(tmp_path):
    a=Archive(tmp_path,reserve=0,quota=100)
    with pytest.raises(RuntimeError,match="RESOURCE"):raw(a)
    (tmp_path/"capture-lost.partial").write_bytes(b"partial")
    with pytest.raises(RuntimeError,match="INCOMPLETE"):Archive(tmp_path,reserve=0).recover()

def test_raw_corruption_detected(tmp_path):
    a=Archive(tmp_path,reserve=0)
    r=raw(a)
    db=sqlite3.connect(next(tmp_path.glob("raw-*.sqlite")))
    db.execute("DROP TRIGGER raw_no_update")
    db.execute("UPDATE raw SET body=x'0000'")
    db.commit()
    db.close()
    with pytest.raises(ValueError,match="HASH"):a.read(r["file"])

class Response:
    status=200
    headers={}
    def __init__(self,body):self.stream=io.BytesIO(body)
    def read(self,n):return self.stream.read(n)
    def __enter__(self):return self
    def __exit__(self,*args):pass

def test_compact_get_allowlist_and_bytes(tmp_path,monkeypatch):
    client=runtime.CompactPublic(tmp_path)
    requests=[]
    def opened(req,**kw):
        requests.append(req)
        return Response(b'{"retCode":0,"time":1000,"result":{}}')
    monkeypatch.setattr(client.opener,"open",opened)
    obj,receipt=client.get("/v5/market/time")
    assert obj["retCode"]==0 and client.archive.read(receipt["file"])[0].startswith(b"{")
    assert requests[0].get_method()=="GET" and requests[0].full_url.startswith("https://api.bybit.com/v5/market/time?")
    for path in ["/v5/order/create","/v5/order/cancel","/v5/account/wallet-balance","https://api-demo.bybit.com/v5/order/create"]:
        with pytest.raises(ValueError):client.get(path)
    with pytest.raises(ValueError):client.get("/v5/market/orderbook",category="linear",symbol="BTCUSDT")
    with pytest.raises(AssertionError):client.get("/v5/market/kline",category="spot",symbol="BTCUSDT",start=1767225600000,end=1767312000000)
    with pytest.raises(RuntimeError):runtime.NoRedirect().redirect_request(None,None,302,"",{},"https://evil.invalid")

@pytest.mark.parametrize("body",[b"not-json",b'{"retCode":10001}',b"x"*1000001],ids=["invalid-json","api-error","oversize"])
def test_failed_http_bodies_retained(tmp_path,monkeypatch,body):
    client=runtime.CompactPublic(tmp_path)
    monkeypatch.setattr(client.opener,"open",lambda *a,**k:Response(body))
    with pytest.raises((ValueError,json.JSONDecodeError)):client.get("/v5/market/time")
    receipt=client.receipts[-1]
    assert client.archive.read(receipt["file"])[0]==body

@pytest.mark.parametrize("point",["none","before_commit","after_commit"])
def test_sequence_parity_recovery_all24(tmp_path,monkeypatch,point):
    root=prepare(tmp_path/"shadow")
    original=ob.Observer(tmp_path/"original")
    adapted=runtime.make_observer(root)
    archive=adapted.api.archive
    for d in range(3):
        day=DAY+datetime.timedelta(days=d)
        cl=np.exp(np.arange(29)[:,None]*np.array([[.01,.02,.03] if d==0 else [.01,-.02,.03] if d==1 else [-.01,-.02,-.03]]))
        sig=ob.signal(cl)
        market,r=markets(archive,100+10*d)
        for instance in [original,adapted]:
            monkeypatch.setattr(instance,"signals",lambda day,sig=sig,r=r:(sig,[r]*3))
            monkeypatch.setattr(instance,"markets",lambda **kw:copy.deepcopy(market))
        monkeypatch.setattr(ob,"utc",lambda:day+datetime.timedelta(seconds=5))
        original.decide(day)
        adapted.decide(day)
        monkeypatch.setattr(ob,"utc",lambda:day+datetime.timedelta(hours=1))
        original.execute(day)
        if d==0 and point=="before_commit":
            event=adapted.store.event
            def crash(eid,kind,payload):
                if kind=="EXECUTION":raise RuntimeError("CRASH_BEFORE_COMMIT")
                event(eid,kind,payload)
            monkeypatch.setattr(adapted.store,"event",crash)
            before=snapshot(adapted.store)
            with pytest.raises(RuntimeError):adapted.execute(day)
            assert snapshot(adapted.store)==before
            monkeypatch.setattr(adapted.store,"event",event)
        if d==0 and point=="after_commit":
            saved=adapted.store.export
            monkeypatch.setattr(adapted.store,"export",lambda:(_ for _ in ()).throw(RuntimeError("CRASH_AFTER_COMMIT")))
            with pytest.raises(RuntimeError):adapted.execute(day)
            monkeypatch.setattr(adapted.store,"export",saved)
        adapted.execute(day)
        before=snapshot(adapted.store)
        adapted.execute(day)
        assert snapshot(adapted.store)==before
        assert snapshot(original.store)==snapshot(adapted.store)
        assert runtime.recover(root)["profiles"]==24
        adapted.store.db.close()
        adapted=runtime.make_observer(root)
    original.store.db.close()
    adapted.store.db.close()

def test_hard_exit_transaction_rollback(tmp_path):
    root=prepare(tmp_path/"hard")
    code="import sqlite3,sys,os; d=sqlite3.connect(sys.argv[1]); d.execute('BEGIN IMMEDIATE'); d.execute(\"UPDATE accounts SET payload='{}'\"); os._exit(23)"
    result=subprocess.run([sys.executable,"-c",code,str(root/"observer.sqlite")],capture_output=True,timeout=10)
    assert result.returncode==23
    assert runtime.recover(root)["profiles"]==24

def test_recovery_rejects_balance_tampering(tmp_path):
    root=prepare(tmp_path/"tamper")
    db=sqlite3.connect(root/"observer.sqlite")
    key,payload=db.execute("SELECT id,payload FROM accounts LIMIT 1").fetchone()
    a=json.loads(payload)
    a["cash"]+=1
    db.execute("UPDATE accounts SET payload=? WHERE id=?",(json.dumps(a),key))
    db.commit()
    db.close()
    with pytest.raises(RuntimeError,match="CHECKPOINT"):runtime.recover(root)

def test_future_t0_calendar_and_no_side_effects(tmp_path):
    assert runtime.add_months(datetime.datetime(2027,8,31,tzinfo=UTC))==datetime.datetime(2028,2,29,tzinfo=UTC)
    for c in [dict(cohort(),t0_utc=None),cohort(DAY-datetime.timedelta(days=2)),dict(cohort(),initial_capital_usdt=1000)]:
        with pytest.raises((ValueError,AttributeError,TypeError)):runtime.validate_cohort(c,now=DAY)
    policy=tmp_path/"policy.json"
    policy.write_text('{"deployment_authorized":false}')
    target=tmp_path/"must-not-exist"
    with pytest.raises(RuntimeError,match="NOT_AUTHORIZED"):runtime.run(target,policy)
    assert not target.exists()

def test_read_only_telemetry_stale_and_no_source_leak(tmp_path):
    root=prepare(tmp_path/"telemetry")
    before=(root/"observer.sqlite").read_bytes()
    data=export(root,root/"telemetry.json",now=DAY)
    assert before==(root/"observer.sqlite").read_bytes()
    assert [p["profile"] for p in data["profiles"]]==PROFILES
    assert all(p["equity_usdt"] is None for p in data["profiles"])
    assert data["comparison"]["r2_minus_t01_usdt"] is None
    assert data["deployment_status"]=="NOT_DEPLOYED" and not data["cohort"]["schedule_active"]
    text=json.dumps(data)
    assert "PREREGISTRATION" not in text and str(root) not in text and "source_code" not in text
    with pytest.raises(ValueError):export(root,root/"observer.sqlite")

def test_raw_crash_after_manifest_removed(tmp_path,monkeypatch):
    a=Archive(tmp_path,reserve=0)
    unlink=Path.unlink
    def fail(path,*args,**kwargs):
        if path.name.startswith("capture-") and path.suffix==".gz":raise RuntimeError("CRASH")
        return unlink(path,*args,**kwargs)
    monkeypatch.setattr(Path,"unlink",fail)
    with pytest.raises(RuntimeError):raw(a)
    monkeypatch.setattr(Path,"unlink",unlink)
    a.recover()
    assert not list(tmp_path.glob("capture-*"))

def test_seal_segment_and_tamper(tmp_path):
    a=Archive(tmp_path,reserve=0)
    source=tmp_path/"source.gz"
    source.write_bytes(gzip.compress(b"frozen",mtime=0))
    r=dict(id="a"*32,available_at="2020-01-01T00:00:00+00:00",sha256=hashlib.sha256(b"frozen").hexdigest(),bytes=6)
    a.pack(source,r)
    sha=a.seal("2020-01-01")
    assert sha==a.seal("2020-01-01")
    with pytest.raises(RuntimeError,match="SEALED"):a.pack(source,dict(r,id="b"*32))
    file=tmp_path/"raw-2020-01-01.sqlite"
    with file.open("ab") as out:out.write(b"tamper")
    with pytest.raises(ValueError,match="SEGMENT"):a.recover()

def test_telemetry_pair_math_and_staleness(tmp_path):
    root=prepare(tmp_path/"pair")
    db=sqlite3.connect(root/"observer.sqlite")
    for i,key in enumerate(PROFILES):
        a=json.loads(db.execute("SELECT payload FROM accounts WHERE id=?",(key,)).fetchone()[0])
        a.update(record_type="FORWARD_EXECUTION",mtm=[305,304,303][i])
        db.execute("INSERT INTO events VALUES (?,?,?,?)",("EXECUTION:2027-01-01:EQUITY:"+key,"EQUITY",DAY.isoformat(),json.dumps(a)))
    db.commit()
    data=export(root,root/"telemetry.json",now=DAY)
    assert data["comparison"]["r2_minus_t01_usdt"]==2
    assert data["comparison"]["r2_minus_t01_pp"]==pytest.approx(2/300*100)
    stale=export(root,root/"telemetry.json",now=DAY+datetime.timedelta(seconds=121))
    assert stale["comparison"]["r2_minus_t01_usdt"] is None
    assert stale["profiles"][0]["equity_usdt"] is None
    db.execute("UPDATE events SET id=replace(id,'2027-01-01','2027-01-02') WHERE id LIKE '%BASELINE:%'")
    db.commit()
    assert export(root,root/"telemetry.json",now=DAY)["comparison"]["r2_minus_t01_usdt"] is None
    db.close()

def test_code_tamper_fails_before_import(monkeypatch):
    manifest=runtime.verify_frozen()
    name=next(iter(manifest["files"]))
    read=Path.read_bytes
    def altered(path):
        b=read(path)
        return b+b"tamper" if path==runtime.REPO/name else b
    monkeypatch.setattr(Path,"read_bytes",altered)
    with pytest.raises(RuntimeError,match="SOURCE_CHANGED"):runtime.verify_frozen()

def test_optimized_python_refused():
    code="import runtime; runtime.frozen_modules()"
    env=dict(os.environ,PYTHONPATH=str(runtime.HERE))
    result=subprocess.run([sys.executable,"-O","-c",code],env=env,capture_output=True,timeout=10)
    assert result.returncode!=0 and b"OPTIMIZED_PYTHON_FORBIDDEN" in result.stderr
